# Pesquisa verificada — o que do guia original vale em 2026-10-24

Duas fontes de verificação:

1. **Pesquisa profunda automatizada** (workflow `deep-research`: 108 agentes, 26 fontes primárias,
   130 afirmações extraídas, 25 verificadas por votação adversarial 3×, 22 confirmadas, 3 derrubadas).
   Cobriu bem as seções 1–3 e 9 do guia (dsd, mwccarm, objdiff, khdays-decomp).
2. **Verificação direta** nesta máquina: API do GitHub, código-fonte nas tags exatas instaladas,
   documentação oficial e **execução** das ferramentas (marcado com 🔬). Cobriu as seções 4–8 que a
   pesquisa automática não conseguiu confirmar.

Legenda: ✅ confirmado · ⚠️ corrigido · ❌ errado no guia · 🔬 testado aqui.

## 1. dsd (AetiasHax/ds-decomp)

| Afirmação do guia | Status | Evidência |
|---|---|---|
| dsd v0.12.1 é a versão atual | ✅ 🔬 | release 2026-09-07; `dsd-windows-x86_64.exe` (sha256 `5917df26…0932`); `dsd --version` → 0.12.1 |
| `rom extract --rom --output-path`, `rom build --config --rom`, `delink -c`, `dis -c -a`, `objdiff -c`, `check modules/symbols`, `apply`, `format`, `sig list/apply` | ✅ 🔬 | README + `--help`; extract/build testados no selftest |
| `init ... --allow-unknown-function-calls` | ✅ (oculta) 🔬 | flag `hide = true` no código (cli/src/cmd/init.rs); aceita pelo binário |
| `dis --ual` | ✅ 🔬 | aparece no `--help` do 0.12.1 |
| `lcf --lcf-file --objects-file` | ❌ | removidas no v0.9.0; hoje `dsd lcf -c <config.yaml>` e os caminhos vêm de `dsd json delinks` |
| `sig new` como opção | ⚠️ | é subcomando: `dsd sig new -c <cfg> -f <função> [-n <idx>]` (+ `sig new-elf`) |
| formatos de `symbols.txt`/`relocs.txt`/`delinks.txt` | ⚠️ | reais: `Nome kind:function(thumb,size=0x6) addr:0x02000066`, `from:0x… kind:arm_call to:0x… module:main`, blocos `arquivo.c:` com `.text start:… end:…` (zeldaret/ph `config/eur/arm9/*`) |
| DS Protect / sig | ✅ | v0.12.0 decripta DS Protect na extração e recripta no build; `dsd fix dsprot`, `fix ov-sigs`, `fix find-exceptix`, `diff new/apply` |
| exige ROM NitroSDK | 🔬 | `dsd init` falha em homebrew ("no pool constants found in static initializer function"); extract/build exigem logo válido e autoloads ITCM/DTCM |

Fontes: <https://github.com/AetiasHax/ds-decomp>, releases v0.12.0/v0.12.1, `cli/src/cmd` na tag v0.12.1,
<https://github.com/zeldaret/ph/blob/main/tools/configure.py>.

## 2. Compiladores Metrowerks (mwccarm)

| Afirmação | Status | Evidência |
|---|---|---|
| 24 builds históricas | ✅ 🔬 | pacote `mwccarm.zip` do decomp.me: `1.2/{base,sp2,sp2p3,sp3,sp4}`, `2.0/{base,sp1,sp1p2,sp1p5,sp1p6,sp1p7,sp2,sp2p2,sp2p3,sp2p4}`, `dsi/{1.1,1.1p1,1.2,1.2p1,1.2p2,1.3,1.3p1,1.6sp1,1.6sp2}` |
| "3.0 build 139" = CW DS 2.0 SP2 patch 4 | ✅ 🔬 | `toolchain/mwccarm/2.0/sp2p4/mwccarm.exe -version` → "Version 3.0 build 139"; decomp.me `mwcc_30_139` |
| esquema de versões | ✅ | CW 1.2.x → "2.0 build N" (72–87), 2.0.x → "3.0 build N" (114–139), DSi → "4.0 build 10xx" |
| `LM_LICENSE_FILE` + `license.dat` | ✅ 🔬 | khdays, mlbis e pret usam; o zip do decomp.me traz `license.dat` (FLEXlm); compila nativo no Windows com ele. O FLEXlm falha intermitente sob paralelismo → a fábrica tenta de novo (como o khdays) |
| fonte dos compiladores | ✅ | decomp.me `github.com/decompme/compilers/releases/download/compilers/mwccarm.zip` (usado aqui) ou `decomp.aetias.com/files/mwccarm.zip` (zeldaret/ph) |
| flags `-O4,p -enum int -proc arm946e -gccext,on -fp soft -lang c99 -Cpp_exceptions off -interworking` | ⚠️ | khdays usa `-O4,p -proc arm946e -interworking -lang c99 -enum int -char signed -inline on,noauto -Cpp_exceptions off -gccext,on` (**sem** `-fp soft`) + `-thumb` por arquivo; ph usa variante C++ |
| link final só com mwldarm (não GNU ld) | ✅ | todos os projetos linkam com `mwldarm` + LCF do `dsd lcf` |
| varredura das 24 versões para achar o compilador | ✅ | khdays `tools/allcc.py`; aqui `factory decomp sweep` |

## 3. objdiff

| Afirmação | Status | Evidência |
|---|---|---|
| objdiff-cli com binário Windows | ✅ 🔬 | v3.8.3 (2026-10-19), `objdiff-cli-windows-x86_64.exe` (exe solto, não zip) |
| `objdiff-cli diff -p . -u UNIT SYMBOL` | ⚠️ 🔬 | existe; sem `-o` é **interativo (TUI)**. Headless: `-o - --format json-pretty` |
| `report generate -o … -f json` | ✅ 🔬 | `-f json\|json-pretty\|proto` |

## 4. Ghidra, dsd-ghidra

| Afirmação | Status | Evidência |
|---|---|---|
| Ghidra 12.1.4 | ✅ (não é a mais nova) | 12.1.4 = 2026-09-21; **12.2** saiu em 2026-10-22. O REA declara suporte verificado a 12.1.x → usado 12.1.4 no WSL |
| dsd-ghidra para Ghidra 12.1.x | ❌ | dsd-ghidra 0.7.0 (2026-08-14) só tem build para **Ghidra 11.2.1** (`extension.properties version=11.2.1`) → Windows usa 11.2.1 |
| dsd-ghidra decripta DS Protect | ✅ | release notes 0.7.0 ("decrypt DS Protect functions", dsd 0.12.0) |
| SyncDsd.java utilizável headless | ❌ 🔬 | usa `GhidraFileChooser` (GUI); e o **loader** abre diálogo modal "Choose CPU program ARM7/ARM9" → `analyzeHeadless` trava. Solução: `FactoryDsLayout.java` / `FactorySyncDsd.java` |
| blocos de overlay sem colisão | ✅ 🔬 | overlay spaces do Ghidra (`ov000::02080000`), reproduzido pelo FactoryDsLayout |

## 5. REA (morluto/rea) e MCPs de Ghidra

| Afirmação | Status | Evidência |
|---|---|---|
| REA existe; npm `rea-agents` | ✅ 🔬 | v6.7.0 (2026-10-18) instalado no WSL; `rea doctor` → provider ghidra saudável (12.1.4, JDK 21) |
| `rea mcp` inicia o servidor | ✅ 🔬 | registro oficial do `rea setup`: comando `rea mcp`; handshake `initialize` respondido via `wsl.exe` |
| `--max-heap-mb`, `--analysis-timeout-ms` no `rea mcp` | ❌ 🔬 | não existem no 6.7.0 (`rea mcp --help`); limites via env (`REA_GHIDRA_STARTUP_TIMEOUT_MS`) |
| env `REA_MCP_INPUT_SCHEMA_PROFILE=compact`, `REA_ANALYSIS_PROVIDER=ghidra`, `REA_GHIDRA_STARTUP_TIMEOUT_MS` | ✅ | docs/installation.md; `compact` já é o padrão |
| Ghidra provider no Windows é "P0 experimental" | ✅ | docs/windows-ghidra-p0.md: **só PE (x86/x64/ARMNT/ARM64) e 6502 cru** → ARM de DS não serve no Windows |
| `open_binary` com `.o` do dsd delink | ✅ | ELF é detectado por header no provider Linux |
| `open_binary` raw | ✅ | `format: raw-binary, architecture: arm, entry_point: 0x…` → linguagem `ARM:LE:32:v7` |
| `snapshot_path` = projeto/`.gzf` do Ghidra | ❌ | é o snapshot de evidências do **REA** (JSON); `close_binary {snapshot_path, overwrite}` salva |
| `set_address_name` grava no symbols.txt | ❌ | renomeia na sessão do provider; o projeto dsd precisa ser editado à parte |
| "207 ferramentas", "economiza ~145.000 tokens" | não verificado | — |
| Alternativas | ✅ | LaurieWired/GhidraMCP 2.0 (precisa GUI), clearbluejar/pyghidra-mcp 0.2.7 (headless), ReVa 7.3.1 (extensão p/ 12.0/12.1/12.1.3) |

## 6. Modding rápido

| Afirmação | Status | Evidência |
|---|---|---|
| NCPatcher, tabela de bytes sobrescritos | ✅ | README v1.1.2: jump ARM→ARM 4 B, ARM→THUMB 4+8 B ponte, THUMB→* 8 B; call 4 B; hook só a partir de ARM (4 + 20 B de ponte) |
| macros `ncp_jump/call/hook/repl/over`, prefixo `t`, `ncp_set_*`, `ncprt_*` | ✅ | README; `ncp.h` incluso no pacote |
| modos `append` (único p/ main) / `replace` / `create`, `arenaLo` | ✅ | README (arenaLo = valor do 1º LDR de `OS_GetInitArenaLo`) |
| NCPatcher não monta a ROM; usa `nds-extract`/`nds-build` do Fireflower | ✅ 🔬 | `fireflower.zip` 1.0 traz `nds-extract.exe`, `nds-build.exe` |
| binários Windows | ✅ 🔬 | NCPatcher `ncpatcher_v1.1.2_Windows-64bit.zip`; armips 0.11.0 (`.7z` com filtro BCJ2 — `py7zr` não abre; o `tar` do Windows sim) |
| neondst | ✅ | v1.0.0 (2026-04-26), exe Windows; init/build/apply/status/decompress |
| ndspy | ✅ 🔬 | 4.2.0; lê ROM, FNT, overlays; traz o logo padrão (usado no selftest) |
| Arm GNU Toolchain | ⚠️ | usado o build **xPack** 15.2.1-1.1 (zip win32-x64 no GitHub), inclui `arm-none-eabi-gdb` |

## 7. Emulação automatizada

| Afirmação | Status | Evidência |
|---|---|---|
| BizHawk como alternativa (não estava no guia) | 🔬 | 2.11.1 (2026-05-01). CLI (ArgParser.cs na tag): `--config --lua --load-state --load-slot --movie --dump-type/-name/-length/-close/-frames --chromeless --gdi --userdata k:v;…` |
| API Lua | 🔬 | `client.screenshot/exit/speedmode/SetSoundOn`, `emu.frameadvance/framecount/limitframerate/getregisters`, `joypad.set/setanalog/get`, `memory.read_u8…u32_le/write_*/read_bytes_as_binary_string`, `savestate.load/save`, `userdata.get`, `input.get` |
| domínios de memória NDS | 🔬 | Main RAM, Shared WRAM, ARM7 WRAM, SRAM, ROM, Instruction TCM, Data TCM, ARM9 BIOS, ARM7 BIOS, Firmware, ARM9 System Bus, ARM7 System Bus |
| roda sem BIOS? | 🔬 | sim (FreeBIOS) **se** a secure area estiver decriptada; criptografada força `UseRealBIOS` (MelonDS.cs) |
| savestate restaura o código antigo da RAM | 🔬 | sim (páginas escritas em runtime); páginas intactas vêm da ROM atual; ROM de tamanho diferente → load recusado |
| melonDS GDB stub | ⚠️ 🔬 | melonDS 1.1 lê `[Instance0.Gdb] Enabled` (não `[Gdb]`: `EmuInstance.cpp` usa `localCfg.GetTable("Gdb")`), `[Instance0.Gdb.ARM9] Port=3333 BreakOnStartup`, `[Instance0.Gdb.ARM7] Port=3334`; modo portátil = pasta `portable` ao lado do exe |
| stub atende várias sessões | ❌ 🔬 | no build Windows só a **1ª conexão** de cada execução recebe resposta (testado terminando com `c`, `D` e close); um `connect()` de sondagem também gasta essa vez → `factory debug start` abre uma ponte persistente (`gdbproxy.py`) e checa a porta por `LISTEN` (psutil), nunca conectando |
| `gdb-multiarch -batch -ex 'target remote …'` | ❌ 🔬 | o stub (`GdbStub.cpp`) consome o 1º byte após o accept como o `+` do handshake; o gdb abre com `$qSupported…` e a sessão trava → cliente RSP próprio (`gdbclient.py`) que manda `+` primeiro |
| CPU com cliente conectado | 🔬 | fica parada dentro do stub até um `c`; a ponte dá `c` ao conectar e interrompe (`\x03`) só durante cada pedido; `halt`/`step`/breakpoint atingido seguram a CPU até `gdb continue` |
| config parcial | 🔬 | `melonDS.toml` incompleto derruba o melonDS na abertura; kill forçado pode truncá-lo → a fábrica gera o padrão (abre/fecha uma vez, backup `melonDS.default.toml`) e fecha com WM_CLOSE |
| melonDS CLI | ✅ | só `rom [gba]`, `-b/--boot auto\|always\|never`, `-f/--fullscreen`, `-a/-A` (sem savestate/Lua) |
| DeSmuME | ✅ | 0.9.13 (2022) é a última release |

## 8. Avaliação por vídeo/imagem

Não há afirmação específica no guia; a abordagem foi desenhada e testada aqui (🔬 selftest):
amostragem ffmpeg (fps uniforme ou `select='gt(scene,X)'`), luminância (preto/branco), movimento entre
amostras (congelamento ≥ N s), diff por pixel + SSIM em blocos 8×8 + dHash, bounding box, contact sheet,
folha [ref | atual | diff] e timeline para o agente ler.

## 9. Estudo de caso

| Afirmação | Status | Evidência |
|---|---|---|
| khdays-decomp 23.125/~23.233 funções em C, 4.716.202/4.728.788 bytes, 306/306 módulos | ✅ | README (2026-10-11) e decomp.dev |
| compilador 3.0 build 139, overrides por arquivo no ov028 | ✅ | `file_compilers.json` (1.2/sp4 e 2.0/sp2p2 em arquivos do DS Protect) |
| `getcand.py`, `match.py`, `verify_idx.py`, `refresh_mismatches.py`, `gen_delinks.py`, `structconv.py`, `gate.sh` | ✅ 🔬 | listados na pasta `tools/` do repositório (API do GitHub) |
| DS Protect do Days é a v1.10 (taxicat1/dsprot branch 1.10) | ⚠️ | o guia falava em 1.28 (Re:coded) |
| licença | ✅ | CC0 (pode-se reaproveitar a abordagem; a fábrica reimplementa o match por função) |
| khdays-port, KH-DAYS-VITA, khrecoded-decomp, ffc-decomp | não verificado | — |

## Pendências (validar com a primeira ROM real)

- `dsd init` / `delink` / `objdiff` / `decomp configure+build` num jogo comercial (o selftest não cobre).
- Cobertura do hotpatch quando o jogo cacheia a FAT ou tem arm9 comprimido grande.
- Tempo do REA na primeira análise de um ELF grande (o REA documenta minutos para binários grandes).
