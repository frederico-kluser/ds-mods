# Avisos de terceiros

O código deste repositório é MIT (veja [LICENSE](LICENSE)), com as exceções e créditos abaixo.

## Código derivado incluído no repositório

### dsd-ghidra — `tools/ghidra_scripts/FactorySyncDsd.java`

Adaptação headless do `SyncDsd.java` do [dsd-ghidra](https://github.com/AetiasHax/dsd-ghidra)
(só o `run()` mudou: sem seletor de arquivo e sem arquivo de propriedades).

```text
MIT License

Copyright (c) 2025 Aetias

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

### Memória CoALA — `.agents/ds-mods-coala-agent-skill/`

Motor `coala.py` e arquivos gerenciados vêm da coala-agent-skill, do mesmo autor deste repositório,
também sob MIT.

## Ferramentas baixadas pelo `factory setup` (não redistribuídas aqui)

O `factory setup` baixa cada ferramenta da fonte oficial para `toolchain/` (fora do git), conferindo o
sha256 fixado em `tools/toolchain.json`. Cada uma mantém sua licença:

| Ferramenta | Licença |
| --- | --- |
| [dsd / ds-decomp](https://github.com/AetiasHax/ds-decomp) | MIT |
| [objdiff](https://github.com/encounter/objdiff), [sjiswrap](https://github.com/encounter/sjiswrap) | MIT ou Apache-2.0 / Apache-2.0 |
| [ninja](https://github.com/ninja-build/ninja) | Apache-2.0 |
| [BizHawk](https://github.com/TASEmulators/BizHawk) | MIT (frontend); os núcleos têm licenças próprias (o núcleo melonDS é GPL-3.0) |
| [melonDS](https://github.com/melonDS-emu/melonDS) | GPL-3.0 |
| [DeSmuME](https://github.com/TASEmulators/desmume) | GPL-2.0 |
| [armips](https://github.com/Kingcom/armips) | MIT |
| [NCPatcher](https://github.com/TheGameratorT/NCPatcher) | GPL-3.0 |
| [Fireflower](https://github.com/MammaMiaTeam/Fireflower) | BSD-3-Clause |
| [neondst](https://github.com/pants64DS/neondst) | GPL-3.0 |
| [xPack GNU Arm Embedded GCC](https://github.com/xpack-dev-tools/arm-none-eabi-gcc-xpack) | GPL-3.0 (GCC/binutils/gdb); scripts xPack MIT |
| [Ghidra](https://github.com/NationalSecurityAgency/ghidra) | Apache-2.0 |
| [dsd-ghidra](https://github.com/AetiasHax/dsd-ghidra) | MIT |
| [Eclipse Temurin JDK 21](https://adoptium.net) | GPL-2.0 com Classpath Exception |
| [REA](https://github.com/morluto/rea) (no WSL) | MIT |
| Compiladores Metrowerks CodeWarrior (`mwccarm`) | proprietários (Metrowerks/Freescale/NXP); pacote comunitário do [decomp.me](https://github.com/decompme/compilers), só na máquina de quem usa |

Dependências Python (instaladas no `.venv` a partir do `requirements.txt` com hashes): numpy
(BSD-3-Clause), Pillow (MIT-CMU), opencv-python-headless (Apache-2.0), PyYAML (MIT),
[ndspy](https://github.com/RoadrunnerWMC/ndspy) (GPL-3.0+), capstone (BSD-3-Clause), pyelftools
(domínio público), imageio-ffmpeg (BSD-2-Clause, traz um binário do ffmpeg sob GPL), py7zr (LGPL-2.1+)
e psutil (BSD-3-Clause).

## Conteúdo da Nintendo

Nenhum conteúdo da Nintendo ou de jogos é versionado. A ROM homebrew do `factory selftest` é código
próprio (`tools/selftest/*.s`); o bitmap do logo exigido no cabeçalho de toda ROM de DS é lido em
tempo de execução do pacote `ndspy` instalado e nunca gravado no repositório.
