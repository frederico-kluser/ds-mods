# games/ — um workspace por jogo

Criado por `factory new <rom>`; o nome é o gamecode (ex.: `YKGP`, `ADAE-r1`).

```
games/<ID>/
├── game.json        metadados da ROM (header, SDK, overlays, hashes, emu_base)         [git]
├── README.md        resumo do workspace                                                  [git]
├── info/            overlays9.tsv, files.tsv (mapa da ROM, grep-ável)                    [git]
├── notes/           descobertas: mapa de RAM, funções, formatos, efeitos dos mods        [git]
├── scenarios/       cenários do ciclo visual (*.json)                                    [git]
├── inputs/          logs de input gravados com `factory play` (replays do boot)          [git]
├── states/          savestates BizHawk (*.State fora do git; index.json no git)
├── mods/<nome>/     mod.json + patches (armips/C/NCPatcher/arquivos) + iterations.jsonl  [git]
├── decomp/          projeto dsd (config/, src/, include/, decomp.json)                   [git parcial]
├── extract/         `dsd rom extract` (binários descomprimidos + NitroFS)               [ignorado]
├── build/           ROM base reconstruída, ROMs dos mods, hotpatch.json                  [ignorado]
├── runs/            saídas do emulador (frames, ram.csv, vídeo, análises)               [ignorado]
├── cache/           baselines do ciclo                                                   [ignorado]
└── ghidra/          projetos Ghidra (Windows)                                            [ignorado]
```

Tudo que é derivado da ROM (código, assets, disassembly, savestates, capturas) fica fora do git.
