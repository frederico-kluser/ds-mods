# Política de segurança

Segurança importa no **ds-mods** (`frederico-kluser/ds-mods`): a ferramenta baixa e executa
toolchains, roda emuladores com scripts Lua, abre uma ponte local para o stub GDB do melonDS e
processa arquivos não confiáveis (ROMs, savestates, vídeos). Obrigado por ajudar a mantê-la segura.

## Versões suportadas

Só a linha mais recente (`main` / última release) recebe correções de segurança.

| Versão | Suportada |
| --- | --- |
| Última release (`main`) | :white_check_mark: |
| Anteriores | :x: |

## Como reportar uma vulnerabilidade

**Não abra issue, discussão nem PR públicos para vulnerabilidades.**

Use o **Private Vulnerability Reporting** do GitHub:
aba **Security** → **Advisories** → **Report a vulnerability**
(<https://github.com/frederico-kluser/ds-mods/security/advisories/new>).

Inclua, se possível: descrição e impacto, passos de reprodução ou prova de conceito, commit/versão
afetados e uma sugestão de correção. Não anexe ROMs comerciais (reproduza com a ROM homebrew do
`factory selftest`).

### Prazos

| Etapa | Meta |
| --- | --- |
| Confirmação de recebimento | **48 horas** |
| Triagem e severidade inicial | **3 dias úteis** |
| Atualizações de status | a cada **7 dias** |
| Correção de achados críticos | **30 dias** |
| Correção dos demais | **90 dias** |

## Divulgação coordenada

Mantenha o achado confidencial até a correção sair e o advisory ser publicado. Combinamos a data de
divulgação, damos crédito no advisory (salvo se preferir anonimato) e avisamos quando a correção for
publicada. Embargo máximo de 90 dias a partir da confirmação.

## Escopo

Dentro do escopo:

- O código deste repositório: CLI `factory` (`tools/factory/`), `tools/lua/factory_runner.lua`,
  `tools/ghidra_scripts/`, `tools/wsl/provision-rea.sh`, os wrappers (`factory`, `factory.cmd`,
  `factory.ps1`, `bootstrap.ps1`) e os workflows de `.github/`.
- O processo de instalação do `factory setup` — por exemplo, um artefato do `tools/toolchain.json`
  cujo sha256 deixasse de ser verificado ou que fosse substituído na origem.
- A ponte GDB local (`tools/factory/gdbproxy.py`), que deve escutar só em `127.0.0.1`.

Fora do escopo (reporte ao projeto de origem):

- Vulnerabilidades no dsd, objdiff, BizHawk, melonDS, armips, NCPatcher, Ghidra, dsd-ghidra, REA,
  ffmpeg, Temurin JDK, GCC/xPack ou nos compiladores Metrowerks.
- Problemas que só afetem versões não suportadas.

## Avisos importantes para quem usa

- O runner Lua do BizHawk lê e escreve arquivos (capturas, RAM, savestates) e o melonDS abre o stub
  GDB em `127.0.0.1:3333`; rode apenas cenários e scripts em que você confia.
- ROMs, savestates e vídeos de terceiros são entradas não confiáveis processadas por emuladores e
  decodificadores; use arquivos de origem conhecida.
- O `.mcp.json` registra o servidor MCP do REA via `wsl.exe`; revise antes de aprovar no Claude Code.

## Proteções do repositório

- Secret scanning e **push protection** ativos.
- **Dependabot** (pip e GitHub Actions) e revisão de dependências nos PRs; dependências Python
  fixadas com hash e o toolchain pinado com sha256.
- **Rulesets** na `main`: PR obrigatório, squash-only, histórico linear, sem force-push e sem
  delete, status check `build` obrigatório; nomes de branch `<tipo>/<slug>`; tags `v*` imutáveis.
- **CodeQL** (Python) e **OpenSSF Scorecard** com resultados publicados.
- **Fuzzing** contínuo (atheris) dos parsers de entrada não confiável: cabeçalho e tabelas de ROM,
  configs do dsd, condições do `ramsearch` e cenários/critérios em JSON (`tests/fuzz/`).
- Actions fixadas por **SHA** e `permissions` mínimas em todos os workflows.

## Contato

Segurança: o canal privado acima. Dúvidas gerais: issues ou Discussions do repositório.
