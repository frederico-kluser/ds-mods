# Decisões de arquitetura (ADR)

Data: 2026-10-24. Máquina de referência: Windows 11 Home 26200, i9-14900HX, 32 GB, WSL2 com
Ubuntu 26.04. Cada decisão foi tomada por evidência (pesquisa + teste nesta máquina); o que foi
testado está marcado com 🔬 e coberto pelo `factory selftest`.

## ADR-01 — Windows nativo para tudo; WSL2 só para o REA

- **Contexto**: o guia original punha Ghidra/REA/dsd/objdiff/NCPatcher no WSL e chamava os `.exe`
  pela ponte de interoperabilidade.
- **Decisão**: dsd, objdiff, ninja, mwccarm, BizHawk, melonDS, armips, NCPatcher, Fireflower, GCC ARM e
  Ghidra 11.2.1 rodam **nativos no Windows** (todos têm binário Windows oficial; o mwccarm é Win32).
  O WSL2 fica só com o **REA 6.7.0 + Ghidra 12.1.4 + JDK 21**.
- **Por quê**: o REA no Windows ("Windows Ghidra P0") só admite executáveis PE e imagens 6502 —
  binários ARM de DS (ELF do dsd delink ou módulo cru) exigem o provider Ghidra no Linux. Fora isso,
  o Windows nativo elimina wibo/Wine, caminhos `/mnt/c` e problemas de interop. 🔬 handshake MCP via
  `wsl.exe -d Ubuntu -- bash -lc "exec rea mcp"` responde com stdout limpo.

## ADR-02 — BizHawk 2.11.1 (núcleo melonDS) como emulador do ciclo automatizado

- **Alternativas**: melonDS 1.1 standalone (sem scripting; só GDB stub e CLI de boot), DeSmuME 0.9.13
  (Lua, mas parado desde 2022), BizHawk (Lua completo + CLI).
- **Decisão**: BizHawk para automação; melonDS standalone para jogar e depurar (GDB); DeSmuME instalado
  como reserva.
- **Por quê** 🔬: `--lua`, `--config`, `--userdata k:v` (passa a config do run ao script),
  `savestate.load/save`, `joypad.set/setanalog` (toque), `client.screenshot`, `memory.read_*` com domínios
  (`ARM9 System Bus`, `Main RAM`…), `memory.read_bytes_as_binary_string` (dump de 4 MB em ~15 ms),
  `client.exit`. Roda **sem BIOS** (FreeBIOS) para ROMs com secure area decriptada. ~160–190 fps sem
  limitador no selftest.

## ADR-03 — Runs "base" usam a ROM base reconstruída pelo dsd

- 🔬 O BizHawk (waterbox) **recusa** carregar um savestate numa ROM de tamanho diferente
  (`savestate.load` → false). O `dsd rom build` gera ROMs com layout/padding próprios.
- **Decisão**: `factory new` faz o round-trip `dsd rom extract → dsd rom build` da ROM original
  (`games/<ID>/build/base/<ID>_base.nds`) e todo run "base" usa essa ROM. Mods também saem do `dsd rom
  build` → mesmo tamanho → savestates gravados na base carregam nas ROMs de mod.
- A ROM original continua disponível como variante `--rom original`.

## ADR-04 — Hotpatch de RAM após carregar savestate

- 🔬 Savestate restaura a RAM com o **código da ROM em que foi gravado** (páginas escritas em runtime,
  como o arm9 descomprimido no boot e overlays carregados). Páginas nunca escritas vêm da ROM atual.
- **Decisão**: `factory mod build` calcula `hotpatch.json` (diferenças dos módulos residentes —
  arm9/ITCM/DTCM/overlays — entre base e mod); o runner Lua aplica após o load e a cada 30 frames,
  só onde a RAM contém os bytes originais; para overlays exige a "guarda" (16 primeiros bytes do overlay
  base) para não escrever no overlay errado. Controle do selftest prova a necessidade (sem hotpatch o
  código antigo roda).
- **Limites**: código anexado (NCPatcher `append`) e mudança de layout de arquivos com FAT em cache →
  usar `start: boot` + inputs, ou `factory state make --rom mod:<m>`.

## ADR-05 — Determinismo

Config do BizHawk gerada por run: `UseRealTime=false`, `InitialTime=2010-01-01`, 3D por software, sem
JIT, `SkipFirmware=true`. 🔬 base vs base = 0% de pixels diferentes; screenshots acompanham cada frame
com latência fixa de 1 frame (o hardware exibe no frame seguinte o que foi desenhado no vblank).

## ADR-06 — Análise de vídeo/imagem local + julgamento pelo próprio agente

- **Decisão**: `factory analyze` usa numpy/Pillow + ffmpeg (do pacote `imageio-ffmpeg`, sem instalação
  global): amostragem uniforme ou por troca de cena, luminância (tela preta/branca), movimento entre
  amostras (congelamentos), diff por pixel/SSIM/dHash contra referência, bounding box das mudanças,
  contact sheet, folha de comparação [referência | atual | diff] e timeline.
- O julgamento semântico ("apareceu o número 999?") é do agente, que **lê os PNGs** (multimodal).
  Nenhuma chamada a API externa de LLM — nada sai da máquina.

## ADR-07 — Cenários JSON, histórico e veredito

Cenário = ponto de partida + inputs + capturas + watches + expectativas. `factory cycle` grava cada
iteração em `mods/<mod>/iterations.jsonl` e dá o veredito relativo ao histórico (SUCESSO, EVOLUIU,
ESTÁVEL, MUDOU, REGREDIU, QUEBROU, PRIMEIRA). Baseline em cache por hash (cenário + ROM + runner).

## ADR-08 — Via A: uma função por arquivo e match por função sem relink

Como o khdays-decomp (CC0): `src/<módulo>/<func>.c`, match compilando só aquela função e comparando com
os bytes do módulo extraído; relocações vêm do `relocs.txt` do dsd (bytes mascarados, alvos comparados
por nome). Rebuild completo opcional via `build.ninja` gerado (padrão do zeldaret/ph: `dsd json delinks`
→ mwcc → `dsd lcf -c` → mwldarm → `dsd rom config/build` → `dsd check modules`). Flags = as do khdays;
compilador padrão 2.0/sp2p4 ("3.0 build 139"); `decomp sweep` descobre versão/flags por jogo.

## ADR-09 — Toolchain fixada e verificada

`tools/toolchain.json` (versão + URL) e `tools/toolchain.lock.json` (sha256 gravado no 1º download;
downloads seguintes precisam bater). Tudo em `toolchain/` (fora do git), sem admin. Atualizar = editar o
manifesto + `factory setup --only <id> --relock`.

## ADR-10 — Ghidra: 11.2.1 no Windows (dsd-ghidra) e layout headless próprio

- dsd-ghidra 0.7.0 só é publicado para Ghidra 11.2.1 → Windows usa 11.2.1; o REA exige 12.1.x → WSL usa
  12.1.4.
- 🔬 O loader do dsd-ghidra abre um diálogo modal ("Choose CPU program ARM7/ARM9") e **trava** o
  `analyzeHeadless`. Para o agente, `FactoryDsLayout.java` monta o programa headless: arm9 + ITCM/DTCM +
  cada overlay num overlay space + blocos de código somente-leitura (constantes dos literal pools
  propagam no decompilador) + símbolos do dsd com modo THUMB. O dsd-ghidra segue para o humano na GUI;
  `FactorySyncDsd.java` é o SyncDsd sem diálogo para programas criados por ele.

## ADR-11 — ROM de selftest própria

ROM homebrew escrita do zero (armips): layout de jogo NitroSDK (secure area decriptada, ModuleParams,
autoloads ITCM/DTCM, overlay, NitroFS) e comportamento observável (cores, barras, placar). O logo do
header é obtido em runtime do pacote `ndspy` instalado (nunca versionado). Valida extract/build do dsd,
mods (armips, bytes, overlay), BizHawk (boot e state), hotpatch, RAM watches, ramsearch, análise,
vídeo, determinismo, mwccarm e Ghidra. Limite: `dsd init` exige o crt0 real do NitroSDK (pulado).

## ADR-12 — Memória e skills por projeto

Memória CoALA local (`.agents/ds-mods-coala-agent-skill/`), skills `nds-visual-loop` e `nds-rea-modder`
em `.agents/skills/` com links em `.claude/skills/`; MCP REA registrado no **projeto** (`.mcp.json`),
não no `~/.claude.json` global como o guia sugeria.

## ADR-13 — Depuração ao vivo: ponte própria para o stub GDB do melonDS

- **Problema**: o stub do melonDS 1.1 (Windows) só atende a primeira conexão de cada execução, mantém
  a CPU parada enquanto há cliente e trava o `arm-none-eabi-gdb` no handshake.
- **Decisão**: `factory debug start` sobe o melonDS e uma ponte (`tools/factory/gdbproxy.py`) que
  guarda essa única conexão; cada `factory gdb status|regs|read|write|break|step|halt|continue` é um
  pedido JSON curto à ponte (127.0.0.1:3340). A CPU só é interrompida durante o pedido, exceto
  `halt`/`step`/breakpoint atingido, que seguram até `continue` (como no gdb).
- **Verificação**: 4 checagens no `factory selftest` (leituras repetidas na mesma sessão, breakpoint
  em `wait_vblank`, escrita + leitura, continue).

## ADR-14 — Publicação aberta com a governança da opensource-project

- **Decisão**: repositório público `frederico-kluser/ds-mods` (MIT) com a mesma governança do projeto
  irmão `snes-mods`: rulesets (main só por PR + squash + check `build`; nomes `<tipo>/<slug>`; tags
  `v*` imutáveis), Conventional Commits, release-please, CodeQL, Scorecard, Dependabot e
  dependências Python fixadas com hash (`requirements*.in` → `pip-compile --generate-hashes`).
- **CI**: Ubuntu, Python 3.12 e 3.13 — ruff, 58 testes unitários sem toolchain (`tests/`, incluindo
  uma imitação fiel do stub GDB do melonDS) e o selftest do motor CoALA. O `factory selftest` (BizHawk,
  melonDS, mwccarm, Ghidra) precisa do toolchain do Windows e roda localmente.
- **Mídia do README**: gerada por `factory selftest-media` — um `factory cycle` de verdade na ROM
  homebrew (nada de jogo comercial).
- **Créditos**: `THIRD_PARTY_NOTICES.md` com o texto MIT do dsd-ghidra (base do `FactorySyncDsd.java`)
  e as licenças de tudo o que o `factory setup` baixa sem redistribuir.

## Desvios em relação ao guia original

| Guia | Aqui | Motivo |
|---|---|---|
| WSL2 para quase tudo | Windows nativo; WSL só REA | ADR-01 |
| Ubuntu 24.04 | Ubuntu 26.04 (já instalado) | funciona igual (JDK 21 via apt) |
| `dsd lcf --lcf-file --objects-file` | `dsd lcf -c` | flags removidas no dsd 0.9.0 |
| `sig new` como flag | subcomando `dsd sig new -c … -f …` | sintaxe real |
| `rea mcp --max-heap-mb … --analysis-timeout-ms …` | `rea mcp` + env em `/etc/profile.d/ds-mods-rea.sh` | o subcomando não tem essas flags |
| `snapshot_path` = `.gzf` do Ghidra | snapshot do próprio REA | docs do REA |
| `open_binary` do `.nds` inteiro proibido | idem, + raw-binary por módulo | docs do REA |
| `-fp soft` nas flags | removido | khdays (projeto completo) não usa |
| SyncDsd headless | não existe → FactorySyncDsd / FactoryDsLayout | diálogos GUI |
| melonDS GDB via WSL + `ip route` | melonDS no Windows, `127.0.0.1:3333` | ADR-01 |
| `gdb-multiarch -batch -ex …` no stub | `factory gdb …` (ponte persistente + cliente RSP próprio) | ADR-13 |
| objdiff GUI em `C:\nds-tools` | tudo em `toolchain\` do projeto | reprodutível/isolado |
