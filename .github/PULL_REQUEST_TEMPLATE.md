<!-- Obrigado por contribuir com o ds-mods!
     O TÍTULO do PR vira a mensagem do squash merge: use Conventional Commits,
     ex.: `feat(cycle): veredito por cenário com critérios visuais`. -->

## O quê

<!-- O que esta mudança faz, em poucas linhas. -->

## Por quê

<!-- Motivação e contexto. Ligue issues com palavras de fechamento: -->

Closes #

## Como

<!-- Abordagem, alternativas consideradas, trade-offs. -->

## Checklist

- [ ] Título no formato Conventional Commits (≤ 72 caracteres).
- [ ] `ruff check tools tests` sem erros.
- [ ] Testes: `python -m unittest discover -s tests -t .` (e `.\factory.ps1 selftest` no Windows com toolchain).
- [ ] Mudança de comportamento tem teste (bug corrigido tem teste de regressão).
- [ ] **Nenhuma** ROM, BIOS, savestate, extração, build, frame/vídeo/print de jogo comercial no diff ou nos anexos.
- [ ] Documentação atualizada onde o comportamento mudou (README, skills em `.agents/skills/`, `CLAUDE.md`/`AGENTS.md`).
- [ ] Memória do projeto exportada se mudou (`factory memory dump`).
- [ ] Workflows alterados: actions fixadas por SHA, `permissions` mínimas, `timeout-minutes`.
- [ ] Mudanças incompatíveis sinalizadas abaixo e com `!`/`BREAKING CHANGE:` no título/corpo.

## Mudanças incompatíveis

<!-- Nenhuma — ou descreva a migração (ex.: formato de cenário, mod.json, comandos do factory). -->

-

## Evidências

<!-- Saída de `factory doctor`/`factory selftest`, relatório de ciclo, folhas de contato da ROM
     homebrew do selftest (nunca de jogos comerciais). -->
