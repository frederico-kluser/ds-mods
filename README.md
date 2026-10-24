# ds-mods — fábrica de mods e recompilação de Nintendo DS

Você entrega a ROM e diz o que quer; o agente (Claude Code) faz engenharia reversa, cria o mod
(ou descompila), roda no emulador a partir de um savestate, **grava e analisa as imagens/vídeo** e
repete sozinho até o efeito aparecer — registrando cada iteração ("evoluiu", "regrediu", "sucesso").

```
ROM ──► factory new ──► dsd extract ──► RE (dis/xrefs/ramsearch/Ghidra/REA)
                                         │
                ┌────────────────────────┴───────────────┐
           Via B: mod (armips/bytes/arquivos/NCPatcher)   Via A: decomp byte-a-byte (dsd+mwccarm)
                └────────────────────────┬───────────────┘
                                factory mod build ──► ROM do mod + hotpatch
                                         │
     savestate ─► BizHawk (Lua): inputs ► screenshots/vídeo ► RAM ► resultado
                                         │
              factory analyze/cycle: compara com a base, detecta travamento/tela preta,
              avalia critérios de RAM e gera compare.png/contact.png ► o agente olha e decide
```

## Como usar (o normal)

1. Coloque a ROM em `roms\` (ex.: `roms\meujogo.nds`).
2. Abra o Claude Code nesta pasta e peça, por exemplo:
   > Registre `roms\meujogo.nds` e faça um mod em que o personagem não perde vida. Teste no emulador.
3. Se o agente pedir um savestate: rode `.\factory.ps1 play`, jogue até o ponto pedido e aperte
   **F11** (salva checkpoint); **End** fecha. É a única ajuda que ele pode precisar.

Para vídeos que você gravar (OBS, celular…): `.\factory.ps1 analyze C:\caminho\video.mp4` — ou só
diga ao agente onde está o vídeo.

## Comandos principais

```powershell
.\factory.ps1 doctor                       # tudo instalado e funcionando?
.\factory.ps1 selftest                     # teste ponta-a-ponta com uma ROM homebrew própria (~45 s)
.\factory.ps1 new roms\meujogo.nds         # registrar a ROM
.\factory.ps1 info --overlays              # header, SDK, overlays
.\factory.ps1 play                         # jogar; F11 = savestate, F10 = dump de RAM, End = sair
.\factory.ps1 scenario new luta --state play_..._cp01
.\factory.ps1 mod new vida-infinita --template armips
.\factory.ps1 cycle luta --mod vida-infinita --note "nop no decremento de HP"
.\factory.ps1 history vida-infinita
.\factory.ps1 analyze video.mp4 --ref outro.mp4
.\factory.ps1 decomp init                  # Via A (descompilação correspondente)
.\factory.ps1 debug start                  # melonDS + GDB ao vivo; depois:
.\factory.ps1 gdb break 0x02000A08 --wait 20; .\factory.ps1 gdb read 0x02100000 64; .\factory.ps1 gdb continue
```

`factory.cmd` (cmd/Explorer) e `./factory` (Git Bash) funcionam igual.

## O que está instalado (tudo em `toolchain\`, fora do git, versões fixadas em `tools\toolchain.json`)

| Ferramenta | Versão | Para quê |
|---|---|---|
| dsd (AetiasHax/ds-decomp) | 0.12.1 | extrair/reconstruir ROM, delink, disassembly, configs de decomp |
| objdiff-cli / GUI | 3.8.3 | diff de objetos e progresso da decomp |
| mwccarm (CodeWarrior DS) | 24 versões | compilador original (match byte-a-byte) |
| ninja | 1.13.2 | build da decomp |
| BizHawk (EmuHawk + melonDS) | 2.11.1 | emulador automatizado (Lua): inputs, capturas, RAM, savestates |
| melonDS | 1.1 | jogar / depurar com GDB stub |
| armips | 0.11.0 | patches em assembly ARM/THUMB |
| NCPatcher + Fireflower | 1.1.2 / 1.0 | injeção de C/C++ |
| xPack arm-none-eabi-gcc | 15.2.1 | GCC/gdb/objdump para ARM |
| Ghidra + dsd-ghidra + JDK 21 | 11.2.1 / 0.7.0 / 21.0.12 | descompilação (GUI e headless) |
| DeSmuME, neondst | 0.9.13 / 1.0.0 | alternativas |
| REA (WSL) + Ghidra + JDK | 6.7.0 / 12.1.4 / 21 | MCP de engenharia reversa para o agente |
| Python (.venv) | 3.12 | ndspy, capstone, pyelftools, numpy, Pillow, OpenCV, imageio-ffmpeg |

Máquina nova: `powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1 -Wsl -SelfTest`.

## Documentação

- `CLAUDE.md` — manual de operação do agente.
- `.agents/skills/nds-visual-loop/SKILL.md` — ciclo emulador → captura → análise → decisão.
- `.agents/skills/nds-rea-modder/SKILL.md` — RE, Via A (decomp), Via B (mods), REA.
- `docs/decisoes.md` — decisões de arquitetura e por quê.
- `docs/pesquisa-verificada.md` — o que do guia original foi confirmado/corrigido (com fontes).
- `docs/guia-original.md` — o guia que originou o projeto.
- `.agents/ds-mods-coala-agent-skill/` — memória CoALA do projeto; o git versiona as notas no dump
  `memory/coala.canonical.jsonl` (`factory memory dump` antes de commitar; `bootstrap.ps1` restaura).

## Legal

Use apenas ROMs/BIOS de cópias que você possui. Nenhum conteúdo da Nintendo ou dos jogos é
versionado aqui; os compiladores Metrowerks vêm do pacote comunitário do decomp.me e ficam só na
sua máquina.
