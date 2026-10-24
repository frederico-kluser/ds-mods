---
name: nds-rea-modder
description: Engenharia reversa, descompilação correspondente (matching decomp byte-a-byte com dsd + mwccarm) e modding rápido (armips, NCPatcher, patches de dados) de jogos de Nintendo DS nesta fábrica, usando o MCP REA (Ghidra 12.1.4 no WSL), o Ghidra 11.2.1 + dsd-ghidra no Windows e os comandos `factory dis/xrefs/find/hookcheck/sym/decomp/mod`. Use SEMPRE que for entender código de um jogo de DS (ARM946E-S ARM/THUMB), achar onde injetar um hook, escrever patch, descompilar uma função até dar MATCH, descobrir o compilador/flags, ou configurar Via A/Via B.
---

# NDS — RE, matching decomp (Via A) e modding rápido (Via B)

Versão corrigida e testada do guia original (`docs/guia-original.md`); o que mudou está em
`docs/pesquisa-verificada.md`. Verdades desta máquina (verificadas em 2026-10-24):

- dsd **v0.12.1**, objdiff-cli **3.8.3**, mwccarm (24 versões, `2.0/sp2p4` = "Version 3.0 build 139"),
  BizHawk **2.11.1**, melonDS **1.1**, armips 0.11, NCPatcher 1.1.2, xPack arm-none-eabi 15.2.1,
  Ghidra **11.2.1 + dsd-ghidra 0.7.0** (Windows), **REA 6.7.0 + Ghidra 12.1.4 + JDK 21** (WSL Ubuntu).
- O REA **no Windows nativo só aceita PE/6502** → para ARM do DS ele roda no WSL (já registrado em
  `.mcp.json` como servidor `rea`, via `wsl.exe -d Ubuntu -- bash -lc "exec rea mcp"`).

## 1. Onde olhar primeiro (barato → caro)

1. `factory info` · `games/<ID>/info/overlays9.tsv` · `files.tsv` · `games/<ID>/notes/`
2. `factory sym <nome|0xADDR>` (símbolos dsd) · `factory dis <nome|0xADDR> [--module ovN]`
3. `factory xrefs <nome|0xADDR>` (relocs do dsd; sem projeto dsd faz scan bruto de BL/BLX + ponteiros)
4. `factory find --string "texto" | --hex "00 00 A0 E3" | --u32 0x0212ABCD [--files]`
5. Pseudocódigo: `factory ghidra decompile <nome|0x..|ovN:0x..|t:0x..>` (Windows, headless) ou o MCP REA
6. Dinâmico: `factory debug start` (melonDS + GDB) → `factory gdb break 0x0201ABCD [--thumb] --wait 20`
   (para e **fica parado**: `gdb regs`, `gdb read 0x02100000 64`, `gdb write <addr> <hex>`, `gdb step 3`)
   → `factory gdb continue` → `factory debug stop`. Não use `arm-none-eabi-gdb` direto no stub: o
   melonDS 1.1 só atende a 1ª conexão e o gdb trava no handshake — `factory gdb` fala com uma ponte
   que guarda essa conexão (`tools/factory/gdbproxy.py`).
7. RAM: `factory ramsearch` (ver skill `nds-visual-loop`)

A saída de **instruções** (dis/REA `instructions`) é a fonte de verdade; o pseudocódigo do Ghidra
adivinha tipos, esconde extensões de sinal e cargas relativas ao PC.

## 2. REA (MCP) — protocolo

- Antes de abrir: `binary_session` (reaproveite a sessão aberta).
- Caminhos são do **Linux**: converta com `factory ghidra wslpath <caminho Windows>` (→ `/mnt/c/...`).
- Alvo preferido: **objeto ELF do `dsd delink`** (`games/<ID>/decomp/build/delinks/**/*.o`) — já traz
  símbolos e marcas `$a/$t` (modo ARM/THUMB) e relocações. `open_binary {path, provider_id: "ghidra"}`.
- Sem projeto dsd: módulo cru com `open_binary {path: ".../extract/arm9/arm9.bin", format: "raw-binary",
  architecture: "arm", entry_point: "0x02000000", provider_id: "ghidra"}` (overlays: o `base_address`
  do `arm9_overlays/overlays.yaml`). A linguagem raw é `ARM:LE:32:v7` (decodifica ARMv5TE/THUMB).
- Nunca abra o `.nds` inteiro no REA: overlays compartilham endereços (0x0214xxxx…) e o import
  headless estoura o tempo. Para visão global com overlays, use o Ghidra Windows (dsd-ghidra põe cada
  overlay no seu overlay space) — `factory ghidra import` + `factory ghidra gui`.
- `snapshot_path` do REA é o snapshot **do próprio REA** (JSON de evidências), não um `.gzf` do Ghidra.
- Renomear no REA (`set_address_name`) vale para a sessão; a fonte de nomes do projeto é o
  `symbols.txt` do dsd: edite-o e rode `dsd format --config-path ...` (via `factory decomp` ou dsd direto).
- Primeiro acesso a um binário grande pode levar minutos (análise do Ghidra); a sessão seguinte é rápida.

## 3. ARM946E-S: ARM/THUMB, literal pools, ponto fixo

- Modo: `symbols.txt` traz `kind:function(arm|thumb,size=0x..)`. Em ponteiros (tabelas, BX/BLX Rn) o
  **bit 0** decide: ímpar = THUMB (endereço real `addr & ~1`).
- C para mwccarm: função THUMB entre `#pragma thumb on` … `#pragma thumb reset`.
- `ldr rX, [pc, #imm]`: valor em `((PC & ~3) + 4 + imm)` (THUMB) ou `(PC + 8 + imm)` (ARM). Se há
  relocação nesse endereço → é símbolo (`&gVar`, `Func`); senão é constante. A ordem do pool segue a
  ordem em que o compilador consome as expressões.
- Ponto fixo fx32 = Q19.12 (`FX32_ONE = 0x1000`). `SMULL lo,hi,a,b; ADDS lo,lo,#0x800; ADC hi,hi,#0;
  MOV r,lo,LSR #12; ORR r,r,hi,LSL #20` = `FX_Mul(a,b)`.
- Divisor/raiz por hardware: `0x04000280` (DIVCNT), `0x04000290` (NUMER), `0x04000298` (DENOM),
  `0x040002A0` (RESULT), `0x040002B0` (SQRTCNT) → `CP_SetDiv*`/`FX_Div` do NitroSDK.
- Mapa: ITCM `0x01FF8000` (32 KB), Main RAM `0x02000000–0x023FFFFF`, DTCM `0x027E0000`/`0x027C0000`,
  I/O `0x04000000`, VRAM `0x06000000+`. Overlays sobrepõem a mesma janela.

## 4. Via A — descompilação correspondente (loop por função)

```powershell
.\factory.ps1 decomp init                 # dsd init + sig apply --all + delink + dis --ual + objdiff.json
.\factory.ps1 decomp candidates --leaf    # folhas pequenas primeiro (khdays: auto/ antes de calls/)
.\factory.ps1 decomp scaffold func_02012345   # src/<módulo>/<func>.c com o assembly comentado
#   edite o .c (tipos de include/types.h; protótipos externos com os nomes do symbols.txt)
.\factory.ps1 decomp match func_02012345  # compila só essa função e compara byte-a-byte
.\factory.ps1 decomp sweep func_02012345 --apply   # 1ª função: descobre versão/flags do mwccarm
.\factory.ps1 decomp status               # progresso por módulo (functions.json)
.\factory.ps1 decomp configure; .\factory.ps1 decomp build   # rebuild completo: delink→mwcc→lcf→mwldarm→check
```

- Compilador: comece com `2.0/sp2p4` (3.0 build 139) e as flags de `decomp.json`; jogos 2004–05 tendem a
  `1.2/*`, 2006–09 a `2.0/*`, DSi/tardios a `dsi/*`. Confirme com `decomp sweep` numa função com
  switch/struct cujo C você confia. As strings `[SDK+NINTENDO:...]` em `factory info` datam o SDK.
- O `match` mascara bytes relocados e exige que os **alvos** batam por nome — chame/acesse em C os
  mesmos nomes do `symbols.txt` (`func_0201xxxx`, `data_0205xxxx`). Literais anônimos `@123` são tolerados.
- Bateu `>>> MATCH <<<` → fica registrado em `decomp/functions.json`. Renomeie com sentido no
  `symbols.txt` quando entender a função.
- `dsd init` só funciona em ROMs NitroSDK (comerciais). Flags escondidas úteis do dsd 0.12.1:
  `init --allow-unknown-function-calls`, `dis --ual`. O `dsd lcf` aceita só `-c` (o guia estava errado).

### Diagnóstico de divergências (mwccarm -O4,p)

| Sintoma no diff | Causa provável | Correção |
|---|---|---|
| registradores trocados (r4↔r5) | ordem de declaração/primeiro uso | reordene declarações; temporária explícita |
| `LSL/ASR #24`, `LSL/LSR #16` extras | tipo 8/16 bits vs int | troque o tipo da variável/iterador ou faça cast |
| `ADD rX,rY,#off` + `LDRH` separado | offset fora do imediato THUMB ou sub-struct | acesse via sub-estrutura/ponteiro local |
| switch com formato diferente | densidade dos `case`, `default` | complete os casos; teste `if/else` |
| `SUB sp,#n` diferente | structs locais/arrays/spills | ajuste locais, remova temporárias redundantes |
| `BGE`↔`BLE`, `BCC`↔`BCS` | operandos invertidos / signed vs unsigned | inverta a comparação; use `u32` |
| pool com offsets trocados | ordem das constantes | reordene as expressões que usam as constantes |

## 5. Via B — mods rápidos (sem descompilar o jogo)

```powershell
.\factory.ps1 mod new hp-infinito --template armips     # ou: data | ncpatcher
.\factory.ps1 hookcheck 0x0213ABCD --kind call [--thumb] [--module ov12]   # SEMPRE antes de um hook
.\factory.ps1 mod build hp-infinito                     # ROM + hotpatch.json
.\factory.ps1 cycle <cenário> --mod hp-infinito --note "..."   # validação no emulador (skill nds-visual-loop)
```

- `mod.json` encadeia passos: `armips` (asm com `.open ARM9_BIN/OV<N>_BIN, base`), `bytes`
  (patch direto com `expect` opcional), `files` (substitui arquivos do NitroFS espelhando `files/`),
  `python` (`def apply(ctx)`: `ctx.read/write(módulo, addr, bytes)`, `ctx.file()`, `ctx.narc()`),
  `ncpatcher` (C/C++ com GCC). Binários do work tree são **descomprimidos**; o `dsd rom build`
  recomprime como o original.
- **Regra ARM × THUMB (NCPatcher)**: a partir de ARM, `ncp_jump/call/hook` sobrescrevem 4 bytes (1
  instrução) — seguro. A partir de THUMB: `call` = 4 bytes, `jump` = 8 bytes, `hook` **não existe**.
  Antes de um `ncp_tcall/ncp_tjump`, o `factory hookcheck` precisa dar SEGURO: nada de partir um `BL`
  de 32 bits, destino de desvio dentro da janela, ou `ldr pc-relativo` sobrescrito sem reimplementar.
- NCPatcher `append` no `main` exige `arenaLo` (endereço do valor carregado pelo 1º `LDR` de
  `OS_GetInitArenaLo`). Código anexado não é hotpatchável — teste esses mods com `start: boot`.
- Dados: tabelas de itens/inimigos costumam estar em arquivos do NitroFS (NARC) — `factory files`,
  `factory find --files --string`, e `ctx.narc()` no passo `python`.

## 6. Persistência

Ao confirmar algo (endereço, função, formato, efeito de um patch), registre em
`games/<ID>/notes/README.md` (tabela do mapa de RAM) **e** na memória CoALA do projeto
(`ds-mods-coala-agent-skill`, `add --type semantic --key <jogo>-<assunto>`), citando a evidência
(run, endereço, saída do `dis`).
