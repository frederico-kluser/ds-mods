# roms/bios/ — BIOS/firmware do DS (opcional, fora do git)

Só necessários quando a ROM tem a **secure area criptografada** (o `factory new` avisa) ou quando você
quiser boot pelo firmware. Use dumps do **seu próprio** Nintendo DS (ex.: com o DSBF dumper/GodMode9i):

| arquivo | conteúdo | tamanho |
|---|---|---|
| `bios7.bin` | BIOS do ARM7 | 16 KiB |
| `bios9.bin` | BIOS do ARM9 | 4 KiB |
| `firmware.bin` | firmware (flash SPI) | 128/256/512 KiB |

Com os três presentes, a fábrica:
- passa `--arm7-bios` ao `dsd rom extract/build` (decripta/recripta a secure area);
- registra os arquivos no BizHawk (`FirmwareUserSpecifications` NDS+bios7/bios9/firmware) e liga
  `UseRealBIOS` para ROMs criptografadas.

Nada nesta pasta (exceto este README) entra no git.
