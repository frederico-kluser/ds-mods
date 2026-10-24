---
name: nds-visual-loop
description: Ciclo autônomo de verificação visual de mods de Nintendo DS nesta fábrica — rodar o emulador (BizHawk + núcleo melonDS) a partir de um savestate ou do boot, injetar inputs, capturar screenshots/vídeo/RAM, comparar com a referência e decidir SOZINHO se o mod evoluiu, regrediu, quebrou ou concluiu. Use SEMPRE que o pedido envolver testar um mod no emulador, "ver se funcionou", gravar ou analisar vídeo/imagens do jogo, criar savestates, montar cenários de teste, achar endereços de RAM (HP, pontos, flags) ou iterar até um efeito acontecer.
---

# Ciclo visual autônomo (emulador → captura → análise → decisão)

O emulador é o juiz. Uma iteração só "funcionou" com **evidência**: critérios de RAM aprovados
**e** você ter olhado as imagens (Read nos PNGs). Nunca declare sucesso só pelo build ter passado.

Comandos (Windows): `.\factory.ps1 <cmd>` · `factory.cmd <cmd>` · Git Bash: `./factory <cmd>`.
Com um só jogo em `games/`, `--game` é opcional.

## Peças

| Peça | Onde | Como cria |
|---|---|---|
| savestate | `games/<ID>/states/<nome>.State` + `states/index.json` | `factory play` (F11) · `factory state make` · `factory state import` |
| log de inputs | `games/<ID>/inputs/<sessão>.txt` (`frame<TAB>botões<TAB>T:x,y`) | `factory play` grava tudo que você/o humano apertou |
| cenário | `games/<ID>/scenarios/<nome>.json` | `factory scenario new <nome> --state <s>` e editar |
| run | `games/<ID>/runs/<data>_<cenário>_<rom>/` (frames/, ram.csv, ram/, events.jsonl, result.json, run.json) | `factory run` / `factory cycle` |
| análise | `<run>/analysis/<vs_ref\|solo>/` report.md, contact.png, compare.png, timeline.png | `factory analyze` / `factory cycle` |
| histórico | `games/<ID>/mods/<mod>/iterations.jsonl` | `factory cycle --mod` (1 linha por iteração, com veredito) |

### Cenário (JSON)

```json
{
  "description": "entrar na batalha e atacar 3x; o mod deve dar dano 999",
  "start": {"type": "state", "state": "battle_start"},
  "frames": 600,
  "inputs": [{"frame": 30, "hold": 5, "buttons": ["A"]}, {"frame": 90, "hold": 3, "touch": [128, 96]}],
  "capture": {"every": 30, "frames": [1, 600], "video": false},
  "watches": [{"name": "enemy_hp", "addr": "0x0212F3A0", "type": "u16"}],
  "watch_every": 10,
  "ram_dumps": [1, 600],
  "save_states": [{"frame": 600, "name": "after_fight"}],
  "expect": {
    "no_freeze": true, "freeze_seconds": 2, "no_black": true,
    "ram": [{"watch": "enemy_hp", "at": "end", "op": "<=", "value": 0}],
    "visual": [{"frame": 600, "region": [0, 0, 256, 192], "vs": "baseline", "min_changed_pct": 2}],
    "notes": "o número de dano '999' aparece sobre o inimigo na tela de cima"
  }
}
```

- `start.type`: `state` (nome do índice ou caminho) **ou** `boot` com `inputs_file` (replay do power-on).
- Botões: `A B X Y L R Start Select Up Down Left Right LidOpen LidClose Power Microphone`; toque em
  coordenadas da tela de BAIXO (0–255, 0–191). `frame` 1 = primeiro frame após o início.
- Capturas são 256×384 (tela de cima em y 0–191, de baixo em y 192–383). Regiões de `visual` usam
  essas coordenadas. A tela mostra o que o jogo desenhou no frame anterior (1 frame de latência).
- `at` aceita `end`, `start`, `max`, `min` ou um número de frame.

## O ciclo (faça sozinho, sem chamar o usuário)

0. **Critério verificável.** Traduza o pedido em: (a) um ou mais watches de RAM com valor esperado e/ou
   (b) uma região da tela que deve mudar, e (c) uma frase em `expect.notes` descrevendo o que você vai
   julgar olhando. Sem critério, não há ciclo — defina-o antes de mexer no mod.
1. **Ponto de partida.** Precisa de um savestate perto do momento a testar. Se não houver, peça ao
   usuário UMA vez para jogar com `factory play` e apertar F11 no ponto certo (é a única ajuda humana
   realmente necessária); ou gere um com `factory state make --inputs inputs/<x>.txt --name <s>`.
2. **Baseline.** `factory cycle <cenário>` (sem `--mod`) roda a ROM base e guarda em cache. Olhe o
   `contact.png` (Read) para confirmar que o cenário mostra o que você espera.
3. **Iteração.** Edite o mod → `factory cycle <cenário> --mod <mod> --note "<o que mudou>"` → leia o
   resumo e faça **Read** em `compare.png` (referência | atual | diff em vermelho) e `contact.png`.
4. **Decida pelo veredito** (comparado com o histórico, não com sua memória):
   - `SUCESSO` (todas as expectativas automáticas passaram) → confirme visualmente `expect.notes`;
     se a imagem confirmar, conclua; se não, ajuste os critérios e continue.
   - `EVOLUIU` (nota maior que a melhor anterior) → continue na mesma direção.
   - `ESTÁVEL` / `MUDOU (mesma nota)` → hipótese errada; volte ao RE (`factory dis/xrefs/ramsearch`).
   - `REGREDIU` → desfaça a última mudança (git diff do mod) e tente outra abordagem.
   - `QUEBROU` (emulador falhou, congelou ou tela preta) → leia `runner.log`, `events.jsonl`,
     registradores (`factory debug start` + `factory gdb regs`, ou `factory gdb break <addr>`), e revise o patch
     (THUMB? hook partindo instrução? `factory hookcheck`).
5. **Parada.** Pare com SUCESSO confirmado, ou após **5 iterações seguidas sem EVOLUIU** — então
   reporte ao usuário o que tentou, com os caminhos dos PNGs/relatórios como evidência.
6. **Registre** (memória CoALA + `games/<ID>/notes/`): endereços confirmados, efeitos, armadilhas.

Use `factory history <mod>` para ver a curva de evolução.

## Achando endereços de RAM (HP, pontos, flags)

1. Cenário com `"ram_dumps": [1, 300]` (ou `factory play` + **F10** nos momentos-chave).
2. `factory ramsearch <run> --type u16 --where "f1 == 100" --where "f300 == 95" --save cand.json`
3. Refine com outro run: `factory ramsearch <run2> --from cand.json --where "f300 < f1" --save cand.json`
4. Confirme pondo o endereço em `watches` e rodando o cenário; depois `factory xrefs 0x0212...`
   (ou `factory find --u32 0x0212...`) para achar o código que lê/escreve aquele valor.

## Savestates × mods (verificado nesta máquina)

- O BizHawk só carrega um savestate numa ROM **do mesmo tamanho**. Por isso todo run "base" usa a
  ROM reconstruída pelo dsd (`build/base/<ID>_base.nds`, mesmo layout das ROMs de mod). Grave states
  sempre via `factory play`/`state make` (nunca de outra ROM).
- O savestate guarda a RAM, ou seja, o **código antigo** já carregado. O runner reaplica o mod na RAM
  (`hotpatch.json`, gerado pelo `factory mod build`) logo após o load e a cada 30 frames (overlays só
  quando o overlay certo está residente — guarda nos 16 primeiros bytes). `result.json → hotpatch`
  mostra `applied/already/mismatch`; `mismatch > 0` = o state não corresponde ao código esperado.
- Não dá para hotpatchar: código **anexado** (NCPatcher `append`, o heap pode estar lá) e mudanças de
  layout de arquivos quando o jogo cacheia a FAT. Nesses casos use `start: boot` + `inputs_file`, ou
  recrie o state na ROM do mod: `factory state make --inputs inputs/<x>.txt --name <s>_mod --rom mod:<m>`.
- Determinismo: RTC fixo (2010-01-01), 3D por software, sem JIT. Base vs base deve dar 0% de
  diferença; se não der, o cenário depende de algo não determinístico.

## Vídeos e imagens externos (gravados por você ou pelo usuário)

```powershell
.\factory.ps1 analyze C:\caminho\gravacao.mp4                 # amostra ~4 fps, contact sheet, travamentos
.\factory.ps1 analyze gravacao.mp4 --scene 0.25               # só trocas de cena (cutscenes, menus)
.\factory.ps1 analyze depois.mp4 --ref antes.mp4              # alinha por tempo e compara
.\factory.ps1 analyze pasta_de_prints\ --ref outra_pasta\     # imagens em ordem alfabética
.\factory.ps1 analyze games\<ID>\runs\<run> --ref games\<ID>\runs\<base>
```

Depois faça Read em `contact.png`/`compare.png` do diretório `*.analysis/`. Vídeo de celular filmando
a tela funciona para presença/ausência de eventos, não para diff pixel a pixel.

## Problemas comuns

| Sintoma | Causa / ação |
|---|---|
| `result.json ausente` / timeout | EmuHawk abriu diálogo (BIOS ausente? ROM criptografada) — veja `emuhawk.stderr.txt`, `runner.log`; rode `factory doctor` |
| `falha ao carregar savestate` | tamanho de ROM diferente do state — use a base reconstruída ou `state make --rom mod:<m>` |
| `hotpatch mismatch` | o state não tem o código base nesse endereço (outro overlay residente?) — confira com `factory dis` |
| tudo "congelado" | jogo esperando input/tela estática legítima; aumente `freeze_seconds` ou mude inputs |
| tela preta | ROM não bootou (secure area criptografada sem BIOS?) ou crash; veja registradores via melonDS GDB |
