//Headless DS memory layout for the ds-mods factory (dsd-ghidra's loader needs a GUI dialog).
//Run as a postScript on a program imported from the extracted arm9.bin (BinaryLoader, base 0x02000000,
//ARM:LE:32:v5t). Args: <layout.json> [analyze]
//layout.json = {"modules":[{"name","kind":"main|itcm|dtcm|overlay","base","file","bss"}...],
//               "symbols":[{"module","addr","kind":"function|data|label","mode":"arm|thumb","size","name"}...]}
//Creates ITCM/DTCM blocks, one OVERLAY block per overlay (own address space: ov012::0214xxxx), uninitialized
//RAM/IO blocks, marks code blocks read-only (so literal-pool constants propagate in the decompiler) and
//applies dsd symbols (THUMB via the TMode context register).
//@category ds-mods
import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.mem.Memory;
import ghidra.program.model.mem.MemoryBlock;
import ghidra.program.model.symbol.SourceType;

import java.io.FileInputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.HashMap;
import java.util.Map;

public class FactoryDsLayout extends GhidraScript {

    private final Map<String, AddressSpace> spaces = new HashMap<>();

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            throw new IllegalArgumentException("uso: FactoryDsLayout <layout.json> [analyze]");
        }
        boolean analyze = args.length > 1 && args[1].equalsIgnoreCase("analyze");
        JsonObject layout;
        try (InputStreamReader r = new InputStreamReader(new FileInputStream(args[0]), StandardCharsets.UTF_8)) {
            layout = JsonParser.parseReader(r).getAsJsonObject();
        }
        Memory mem = currentProgram.getMemory();
        AddressSpace ram = currentProgram.getAddressFactory().getDefaultAddressSpace();
        long mainEnd = 0x02000000L;
        for (JsonElement e : layout.getAsJsonArray("modules")) {
            JsonObject m = e.getAsJsonObject();
            String name = m.get("name").getAsString();
            String kind = m.get("kind").getAsString();
            long base = m.get("base").getAsLong();
            String file = m.get("file").getAsString();
            long size = Files.size(Paths.get(file));
            Address start = ram.getAddress(base);
            if (kind.equals("main")) {
                MemoryBlock b = mem.getBlock(start);
                if (b == null) {
                    throw new IllegalStateException("bloco principal não encontrado em " + start);
                }
                b.setName(name);
                b.setRead(true);
                b.setWrite(false);
                b.setExecute(true);
                spaces.put(name, ram);
                mainEnd = base + size;
                continue;
            }
            boolean overlay = kind.equals("overlay");
            MemoryBlock b = mem.getBlock(name);
            if (b != null) {           // re-run (symbol sync): keep existing blocks
                spaces.put(name, overlay ? b.getStart().getAddressSpace() : ram);
                continue;
            }
            try (InputStream in = new FileInputStream(file)) {
                b = mem.createInitializedBlock(name, start, in, size, monitor, overlay);
            }
            b.setRead(true);
            b.setWrite(kind.equals("dtcm"));
            b.setExecute(!kind.equals("dtcm"));
            spaces.put(name, overlay ? b.getStart().getAddressSpace() : ram);
            println("bloco " + name + " @ " + b.getStart() + " (" + size + " bytes" + (overlay ? ", overlay" : "") + ")");
        }
        // the rest of main RAM (heap/bss) and I/O as uninitialized blocks: references resolve to memory
        long ramTop = 0x02400000L;
        long gapStart = (mainEnd + 0xFFF) & ~0xFFFL;
        if (gapStart < ramTop && mem.getBlock(ram.getAddress(gapStart)) == null) {
            MemoryBlock b = mem.createUninitializedBlock("mainram", ram.getAddress(gapStart), ramTop - gapStart, false);
            b.setRead(true);
            b.setWrite(true);
        }
        if (mem.getBlock(ram.getAddress(0x04000000L)) == null) {
            MemoryBlock io = mem.createUninitializedBlock("io", ram.getAddress(0x04000000L), 0x2000, false);
            io.setRead(true);
            io.setWrite(true);
            io.setVolatile(true);
        }
        int nf = 0, nd = 0, bad = 0;
        Register tmode = currentProgram.getProgramContext().getRegister("TMode");
        JsonArray syms = layout.has("symbols") ? layout.getAsJsonArray("symbols") : new JsonArray();
        for (JsonElement e : syms) {
            JsonObject s = e.getAsJsonObject();
            AddressSpace sp = spaces.getOrDefault(s.get("module").getAsString(), ram);
            Address a = sp.getAddress(s.get("addr").getAsLong() & ~1L);
            String name = s.get("name").getAsString();
            String kind = s.get("kind").getAsString();
            try {
                if (kind.equals("function")) {
                    boolean thumb = s.has("mode") && s.get("mode").getAsString().equals("thumb");
                    long size = s.has("size") && !s.get("size").isJsonNull() ? s.get("size").getAsLong() : 2;
                    if (tmode != null) {
                        currentProgram.getProgramContext().setValue(tmode, a, a.add(Math.max(size, 2) - 1),
                            thumb ? BigInteger.ONE : BigInteger.ZERO);
                    }
                    disassemble(a);
                    Function f = getFunctionAt(a);
                    if (f == null) {
                        f = createFunction(a, name);
                    } else {
                        f.setName(name, SourceType.IMPORTED);
                    }
                    nf++;
                } else {
                    createLabel(a, name, true, SourceType.IMPORTED);
                    nd++;
                }
            } catch (Exception ex) {
                bad++;
            }
        }
        println(String.format("símbolos aplicados: %d funções, %d dados, %d falhas", nf, nd, bad));
        if (analyze) {
            println("rodando auto-análise (pode demorar)…");
            analyzeAll(currentProgram);
        }
    }
}
