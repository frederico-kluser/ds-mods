# roms/ — ROMs base (fora do git)

Coloque aqui o dump **da sua própria cópia** do jogo (`.nds`). Tudo nesta pasta é ignorado pelo git,
exceto este README e `bios/README.md`.

```powershell
.\factory.ps1 new roms\meu_jogo.nds     # registra o jogo em games\<GAMECODE>\ e extrai com dsd
```

O `factory new` também aceita uma ROM em qualquer outro lugar: ela é copiada para `roms\<GAMECODE>.nds`.

- ROM "decrypted" (secure area começando em `E7FFDEFF`) roda direto no BizHawk/melonDS sem BIOS.
- ROM "encrypted" (dump No-Intro padrão) exige BIOS reais do seu console — veja `bios/README.md`.
  O `factory new` diz qual é o caso.
