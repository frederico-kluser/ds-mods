//Headless decompilation for the ds-mods factory.
//Args: <out.txt> <target>...   target = name | 0x02001234 | t:0x02001234 (THUMB) | ov12:0x02140000 | ov012::02140000
//Writes the C of every target to out.txt (and the console). Missing functions are created on the fly
//(raw imports), so the program should be opened with -readOnly when nothing must be persisted.
//@category ds-mods
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileOptions;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.lang.Register;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolIterator;

import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.Writer;
import java.math.BigInteger;
import java.nio.charset.StandardCharsets;

public class FactoryDecompile extends GhidraScript {

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 2) {
            printerr("uso: FactoryDecompile <saida.txt> <alvo>...");
            return;
        }
        DecompInterface di = new DecompInterface();
        DecompileOptions opts = new DecompileOptions();
        di.setOptions(opts);
        di.toggleCCode(true);
        di.toggleSyntaxTree(false);
        di.openProgram(currentProgram);
        StringBuilder sb = new StringBuilder();
        for (int i = 1; i < args.length; i++) {
            String target = args[i];
            Function f = resolve(target);
            if (f == null) {
                sb.append("// ").append(target).append(": função não encontrada\n\n");
                continue;
            }
            DecompileResults r = di.decompileFunction(f, 180, monitor);
            sb.append("// ===== ").append(f.getName()).append(" @ ").append(f.getEntryPoint())
              .append(" (").append(f.getBody().getNumAddresses()).append(" bytes) =====\n");
            if (r != null && r.decompileCompleted() && r.getDecompiledFunction() != null) {
                sb.append(r.getDecompiledFunction().getC()).append("\n");
            } else {
                sb.append("// decompilação falhou: ").append(r == null ? "?" : r.getErrorMessage()).append("\n\n");
            }
        }
        di.dispose();
        try (Writer w = new OutputStreamWriter(new FileOutputStream(args[0]), StandardCharsets.UTF_8)) {
            w.write(sb.toString());
        }
        println(sb.toString());
    }

    private Function resolve(String target) throws Exception {
        FunctionManager fm = currentProgram.getFunctionManager();
        boolean thumb = false;
        String space = null;
        String text = target;
        if (text.startsWith("t:")) {
            thumb = true;
            text = text.substring(2);
        }
        int colon = text.indexOf(':');
        if (colon > 0) {
            space = text.substring(0, colon);
            text = text.substring(text.lastIndexOf(':') + 1);
        }
        if (text.matches("(0x)?[0-9a-fA-F]{6,8}")) {
            long value = Long.parseLong(text.startsWith("0x") ? text.substring(2) : text, 16);
            if ((value & 1) == 1) {
                thumb = true;
                value &= ~1L;
            }
            Address a = null;
            if (space != null) {
                for (AddressSpace sp : currentProgram.getAddressFactory().getAllAddressSpaces()) {
                    if (matchesSpace(sp.getName(), space)) {
                        a = sp.getAddress(value);
                        break;
                    }
                }
            } else {
                a = currentProgram.getAddressFactory().getDefaultAddressSpace().getAddress(value);
            }
            if (a == null) {
                return null;
            }
            Function f = fm.getFunctionContaining(a);
            if (f == null) {
                Register tmode = currentProgram.getProgramContext().getRegister("TMode");
                if (tmode != null && thumb) {
                    currentProgram.getProgramContext().setValue(tmode, a, a, BigInteger.ONE);
                }
                disassemble(a);
                f = createFunction(a, null);
            }
            return f;
        }
        for (Function f : fm.getFunctions(true)) {
            if (f.getName().equals(text)) {
                return f;
            }
        }
        SymbolIterator it = currentProgram.getSymbolTable().getSymbols(text);
        while (it.hasNext()) {
            Symbol s = it.next();
            Function f = fm.getFunctionAt(s.getAddress());
            if (f != null) {
                return f;
            }
        }
        return null;
    }

    private static boolean matchesSpace(String spaceName, String wanted) {
        String a = spaceName.toLowerCase().replaceAll("[^a-z0-9]", "");
        String b = wanted.toLowerCase().replaceAll("[^a-z0-9]", "");
        if (a.equals(b)) {
            return true;
        }
        // ov12 vs ov012 / arm9_ov012 / overlay_12
        String da = a.replaceAll("\\D", "").replaceFirst("^0+(?=\\d)", "");
        String db = b.replaceAll("\\D", "").replaceFirst("^0+(?=\\d)", "");
        return !db.isEmpty() && da.equals(db) && a.contains("ov") && b.contains("ov");
    }
}
