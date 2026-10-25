# Contribuindo com o ds-mods

Obrigado pelo interesse! Este guia explica o ambiente, o fluxo de trabalho e a régua de qualidade de
qualquer mudança em `frederico-kluser/ds-mods`. Ao participar, você concorda com o
[Código de Conduta](CODE_OF_CONDUCT.md).

## Regra número zero: nada de material de jogos comerciais

**Nunca** inclua em commits, issues, PRs ou anexos: ROMs, BIOS/firmware, savestates, extrações do
`dsd`, builds de mods, frames/vídeos/prints de jogos comerciais, projetos/exports do Ghidra ou trechos
grandes de código desmontado. O `.gitignore` bloqueia os formatos conhecidos. Para reproduzir um
problema, use a ROM homebrew do próprio repositório (`factory selftest`, fontes em `tools/selftest/`)
ou descreva o jogo (título, região, gamecode, revisão), sem anexar a ROM.

## Ambiente

Requisitos: **Windows 10/11 x64** (o toolchain pinado em `tools/toolchain.json` é para Windows),
**Python 3.11–3.13**, **Git** e, opcionalmente, **WSL2 com Ubuntu** (servidor MCP do REA).

```powershell
git clone https://github.com/<seu-usuario>/ds-mods.git
cd ds-mods
git remote add upstream https://github.com/frederico-kluser/ds-mods.git

powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1   # .venv + toolchain (sha256) + doctor
.\factory.ps1 doctor                                         # tudo deve sair OK
.\factory.ps1 selftest                                       # ponta a ponta com a ROM homebrew (~45 s)
```

No cmd use `factory.cmd`; no Git Bash, `./factory`. Ferramentas de desenvolvimento:
`.venv\Scripts\pip install --require-hashes -r requirements-dev.txt`. As dependências são fixadas com
hash a partir de `requirements*.in` (`pip-compile --generate-hashes`); o Dependabot mantém os dois.

**Linux/macOS**: os testes unitários rodam (`python -m unittest discover -s tests -t .`); o
`factory selftest` precisa do toolchain do Windows. Contribuições que levem o `tools/toolchain.json`
a outras plataformas são muito bem-vindas.

## Como enviar uma mudança

- **Nunca faça push direto na `main`** — um ruleset do GitHub recusa. Tudo entra por pull request
  com **squash merge**.
- Trabalhe num fork ou numa branch curta `<tipo>/<slug>` e abra o PR contra `main`.
- Um assunto por PR; mantenha a branch atualizada com rebase sobre `upstream/main`.

### Nomes de branch (impostos pelo GitHub)

`<tipo>/<slug>` com tipos fechados: `feat` `fix` `docs` `style` `refactor` `perf` `test` `build`
`ci` `revert` `hotfix` `release` — ex.: `feat/thumb-hook-check`, `fix/hotpatch-overlay-guard`,
`docs/guia-video`. Slug curto em kebab-case. Só branches de automação (`dependabot/*`, `renovate/*`,
`release-please--*`) fogem à regra.

### Mensagens de commit (Conventional Commits)

O título do PR vira a mensagem do squash e alimenta o changelog e a versão (SemVer):

```text
feat(cycle): veredito por cenário com critérios visuais
fix(emu): hotpatch respeita a guarda do overlay
docs: guia de análise de vídeo
feat!: novo formato do cenário         ← quebra compatibilidade (MAJOR)
```

Tipos: `feat` `fix` `docs` `style` `refactor` `perf` `test` `build` `ci` `chore` `revert`;
cabeçalho com no máximo **72 caracteres**. O job `build` do CI valida o título do PR e os commits.

## Régua de qualidade (antes de abrir o PR)

```powershell
ruff check tools tests                          # lint (config em ruff.toml)
python -m unittest discover -s tests -t .       # testes unitários (qualquer SO)
.\factory.ps1 selftest --ghidra                 # + ponta a ponta (Windows com toolchain)
```

- Toda mudança de comportamento vem com teste; bug corrigido vem com teste de regressão.
- Mudanças no emulador, no ciclo ou na análise devem ser exercitadas com a ROM homebrew do
  `factory selftest`; se a mídia do README mudar, regenere com `factory selftest-media`.
- Se o comportamento mudar, atualize a documentação dos agentes: as skills em `.agents/skills/`
  (`nds-visual-loop`, `nds-rea-modder`) e o `CLAUDE.md`/`AGENTS.md`.
- Aprendizado durável vai para a memória CoALA do projeto; antes de commitar, `factory memory dump`.
- Workflows: actions fixadas por SHA, `permissions` mínimas e `timeout-minutes` em todo job.

### Fuzzing

Os parsers que recebem entrada de fora (cabeçalho e tabelas de ROM, configs do dsd, condições do
`ramsearch`, cenários e critérios em JSON) têm alvos de fuzzing em `tests/fuzz/` (atheris/libFuzzer).
O CI roda cada alvo nos PRs que mexem em `tools/factory/` e por 10 minutos toda semana. Localmente:

```bash
# Linux x86-64 ou WSL: libFuzzer guiado por cobertura (o 1.º diretório recebe as entradas novas)
pip install --require-hashes -r requirements.txt -r requirements-fuzz.txt
python tests/fuzz/fuzz_rom.py -max_total_time=60 work/fuzz/rom tests/fuzz/corpus/rom

# Windows/macOS (sem atheris): corpus + mutações aleatórias; também reproduz um crash baixado do CI
python tests/fuzz/fuzz_rom.py crash-0123abcd
```

Crash corrigido vira teste de regressão em `tests/test_robustez.py`, e a entrada vai para
`tests/fuzz/corpus/<alvo>/`.

## Revisão, merge e releases

- O PR só entra com o status check `build` verde (lint + testes + mensagens convencionais) e as
  conversas resolvidas; o merge é sempre squash.
- As releases são automáticas: o **release-please** mantém um PR de release com a próxima versão
  (calculada pelos commits) e o `CHANGELOG.md`. Não edite o changelog à mão.
- Tags `v*` são imutáveis.

## Licença

Ao contribuir, você concorda que sua contribuição é licenciada sob a [MIT](LICENSE), como o restante
do projeto. Ferramentas de terceiros baixadas pelo `factory setup` mantêm suas próprias licenças e não
são redistribuídas por este repositório (veja [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)).
