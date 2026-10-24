# Guia original (como recebido em 2026-10-24)

> Este é o guia que originou o projeto, preservado como referência. Ele foi **verificado e corrigido**:
> veja `docs/pesquisa-verificada.md` (o que vale, o que mudou) e `docs/decisoes.md` (o que foi adotado).
> A formatação (títulos, tabelas e blocos de código) foi reconstruída; o texto é o original, exceto o
> Módulo 5 (CLAUDE.md/SKILL.md prontos), resumido aqui porque foi incorporado — corrigido — ao
> `CLAUDE.md` e à skill `nds-rea-modder`.

# Engenharia Reversa Autônoma e Descompilação Correspondente de Nintendo DS: Arquitetura Híbrida Windows 11, WSL2, REA MCP e Toolchain dsd

A convergência entre agentes autônomos baseados em Modelos de Linguagem de Grande Escala (LLMs) operando via Model Context Protocol (MCP) e ferramentas determinísticas de engenharia reversa redefiniu a velocidade e a precisão da reconstrução de código-fonte em sistemas embarcados legados. No ecossistema do Nintendo DS (NDS), historicamente considerado complexo devido à sua arquitetura de processador duplo assimétrico (ARM946E-S e ARM7TDMI), alternância constante de conjuntos de instruções (ARM de 32 bits e Thumb de 16 bits) e sobreposição massiva de memória via overlays, a transição de fluxos manuais para pipelines automatizados permitiu comprimir cronogramas de vários anos em poucos meses.

Este relatório técnico estabelece a arquitetura definitiva para engenharia reversa, descompilação correspondente (matching decompilation byte-a-byte) e criação de modificações (ROM hacking e injeção de código) para jogos de Nintendo DS utilizando o Windows 11 em topologia híbrida com WSL2 (Ubuntu 24.04), o servidor MCP REA (Reverse Engineer Anything — morluto/rea) integrado ao Ghidra 12.1.4, e a stack moderna composta por dsd (AetiasHax/ds-decomp), dsd-ghidra, objdiff-cli e NCPatcher.

## Módulo 1: Estudo de Caso Profundo — Kingdom Hearts 358/2 Days (khdays-decomp) e a Toolchain dsd

### Engenharia de Velocidade: Como khdays-decomp Atingiu ~100% em Três Meses

O projeto de descompilação correspondente de Kingdom Hearts 358/2 Days (Yokimitsuro/khdays-decomp), focado na revisão europeia da ROM (YKGP, days.nds), constitui um marco na engenharia reversa assistida por ferramentas modernas. O repositório alcançou a marca de 23.125 de ~23.233 funções reconstruídas em código C real (~99,5% do total de funções), correspondendo a 4.716.202 de 4.728.788 bytes de código executável (~99,73% em bytes de C puro), restando apenas 108 funções (~0,5%) classificadas como rotinas de biblioteca originalmente escritas em Assembly pelo fabricante ou exceções inline estritamente autorizadas, especificamente duas funções do motor de jogo que exigem a instrução de hardware clz (Count Leading Zeros). A composição global do repositório reflete essa pureza estrutural, sendo constituída por 95,8% de código C, 2,8% de ferramentas em Python, 1,0% de Assembly, 0,3% de C++ e 0,1% de Lua.

A velocidade extraordinária dessa descompilação — saindo do zero até a paridade funcional completa de mais de 300 módulos e viabilizando imediatamente o port nativo de PC (Yokimitsuro/khdays-port, compilado em Visual Studio 2026 para x64 via reescrita de ponteiros com pycparser), o port nativo de PlayStation Vita (ggdrn/KH-DAYS-VITA, construído sobre VitaSDK e vitaGL) e o reaproveitamento mecânico de código para Kingdom Hearts Re:coded (Yokimitsuro/khrecoded-decomp via tools/port_days.py) — resulta de quatro decisões arquiteturais interligadas.

A primeira decisão estrutural consiste no isolamento atômico de uma função por ficheiro (one-function-per-file). Ao contrário de projetos clássicos de descompilação que tentam reconstruir imediatamente unidades de tradução (.c) monolíticas com dezenas de funções interdependentes, o khdays-decomp isola cada função no seu próprio ficheiro .c nomeado com o identificador da função dentro do diretório do respetivo módulo (src/engine/ para o binário principal e ITCM, ou src/overlays/<categoria>/<ovNNN>/ para cada overlay). Essa atomização elimina conflitos de merge, impede que o erro de alocação de registos numa função bloqueie a integração de funções vizinhas e permite que agentes de IA operem em lotes paralelos sem efeitos colaterais no restante da unidade de tradução.

A segunda decisão reside no particionamento topológico entre funções folha (auto/) e funções com chamadas externas (calls/). Através dos scripts tools/find_candidates.py e tools/find_calls.py, o grafo de chamadas do binário é particionado antes da intervenção humana ou do agente. Funções folha (leaf functions — que não realizam chamadas externas e dependem apenas de aritmética local e registos) são segregadas em diretórios auto/, enquanto funções com dependências externas ficam em calls/. Essa separação permite atacar primeiro milhares de funções folha com contexto mínimo de cabeçalhos, consolidando tipos básicos e estruturas locais antes de abordar funções complexas de jogabilidade.

O terceiro pilar é o mecanismo de exclusão dinâmica de divergências no linker mediante a articulação entre refresh_mismatches.py e gen_delinks.py. Num projeto com mais de 300 módulos, manter a ROM inteira compilável enquanto funções estão em progresso é essencial. O script python tools/refresh_mismatches.py compila toda a árvore de código e regista qualquer função divergente em build/known_mismatches.txt. Em seguida, tools/gen_delinks.py lê essa lista e exclui automaticamente as descompilações defeituosas do processo de linking, instruindo o dsd a preencher a lacuna exata com os bytes originais da ROM. Assim, o verificador global (bash tools/gate.sh) reconstrói todos os módulos e garante a integridade binária do programa completo continuamente.

O quarto mecanismo é o oráculo de verificação unitária instantânea formado por getcand.py, match.py e structconv.py. Para evitar que o agente invoque o build completo a cada tentativa, python tools/getcand.py func_XXXXXXXX extrai instantaneamente o disassembly, as relocalizações e a linha de comando exata de verificação para a função alvo. O script python tools/match.py <ficheiro.c> invoca o mwccarm.exe apenas sobre o ficheiro candidato e compara os bytes gerados contra o binário original, imprimindo >>> MATCH <<< em milissegundos quando a paridade byte-a-byte é atingida. Quando estruturas parciais locais precisam ser promovidas para declarações partilhadas num cabeçalho global em include/game/, o utilitário tools/structconv.py refatora as declarações garantindo que todas as funções dependentes continuem gerando exatamente os mesmos bytes.

### Estrutura Exata de Diretórios e Ficheiros de Configuração do dsd

O alicerce estrutural tanto do khdays-decomp quanto de projetos correlatos (khrecoded-decomp, zeldaret/ph, mlbis-decomp) é o dsd (AetiasHax/ds-decomp, versão 0.12.1), escrito em Rust. O fluxo de inicialização extrai a ROM bruta, analisa os limites de código sem intervenção manual e gera a topologia de configuração através da sequência de comandos abaixo:

```bash
# 1. Extração da ROM bruta (descomprime arm9, arm7 e overlays)
tools/dsd rom extract --rom days.nds --output-path dsd_extract/

# 2. Inicialização heurística do projeto dsd (análise de fluxo, limites de função e relocalizações)
tools/dsd init --rom-config dsd_extract/config.yaml --output-path config/ --build-path build/ --allow-unknown-function-calls

# 3. Separação de unidades de tradução (delinking) em objetos ELF relocalizáveis (.o)
tools/dsd delink --config-path config/arm9/config.yaml

# 4. Geração do disassembly unificado (.s) para inspeção e montagem de stubs
tools/dsd dis --config-path config/arm9/config.yaml --asm-path asm/ --ual
```

A tabela a seguir detalha a hierarquia de diretórios e o papel técnico de cada ficheiro de configuração gerado e mantido no repositório:

| Caminho no Repositório | Natureza do Artefato | Função Arquitetural e Regras de Sintaxe no Ecossistema dsd / khdays-decomp |
|---|---|---|
| `dsd_extract/config.yaml` | Manifesto de Extração (Gerado, Gitignored) | Gerado por dsd rom extract. Define offsets físicos, endereços base de RAM do arm9.bin, arm7.bin, ITCM, DTCM e a tabela de overlays (y9.bin). |
| `config/arm9/config.yaml` | Configuração Mestre do Processador ARM9 | Gerado por dsd init. Aponta para os caminhos de delinks.txt, symbols.txt e relocs.txt do módulo principal (main), itcm, dtcm e de todos os subdiretórios em config/arm9/overlays/ovNNN/. |
| `config/arm9/file_compilers.json` | Override de Compilador por Unidade | Exclusivo de projetos avançados como khdays-decomp. Permite especificar versões alternativas do mwccarm para unidades de tradução de middleware pré-compiladas (ex: núcleo criptográfico anti-tamper no ov028). |
| `config/**/delinks.txt` | Mapa de Secções e Divisão de Ficheiros (.o) | Define os intervalos de memória (.text, .rodata, .data, .bss, .init, .ctor, .exception, .exceptix) ordenados por endereço e a lista de ficheiros divididos ordenada pela ordem de link. Suporta empréstimo de secções (section borrowing, ex: alocar variáveis .bss do DTCM a partir de outros módulos) e tags categories: gfx, core para relatórios do objdiff. |
| `config/**/symbols.txt` | Tabela de Símbolos Ordenada por Endereço | Lista todos os símbolos de código e dados do módulo com atributos chave-valor: `<nome> kind:<function\|data> addr:0xXXXXXXXX size:0xXXXX mode:<arm\|thumb> <global\|local\|weak\|ambiguous>`. Ponteiros em .ctor recebem o prefixo .p__sinit_... e inicializadores estáticos em .init usam __sinit_.... |
| `config/**/relocs.txt` | Tabela de Relocalizações Inter- e Intra-Módulos | Ordenada pelo endereço de origem (`from:0xXXXXXXXX kind:<tipo_reloc> to:0xXXXXXXXX add:0xXX module:<alvo>`). Mapeia referências de ponteiros e chamadas BL/BLX entre main e overlays (ou para IDs de overlays), incluindo candidatos múltiplos quando o destino em RAM é compartilhado por vários overlays. |
| `src/engine/` & `src/overlays/` | Código-Fonte C do Jogo (1 função por .c) | src/engine/ aloja o motor principal e ITCM; src/overlays/ subdivide os mais de 300 módulos funcionalmente em scenes/, screens/, field/, players/, enemies/ovNNN_enemy_<id> e system/. |
| `libs/nitro/`, `nns/`, `msl/`, `mobiclip/` | Código C/ASM de SDK e Middleware | Contém as implementações das bibliotecas oficiais da Nintendo e da Metrowerks divididas em auto/ (folhas), calls/ (com chamadas) e asm_stubs/ (rotinas em Assembly nativo do SDK). |
| `sdk/` | Harness de Identificação do NitroSDK | Ambiente isolado que compila o código-fonte do NitroSDK já descompilado por outros projetos (como pret/pokediamond) para cruzar hashes de bytes contra a ROM alvo. |

### Identificação e Separação do NitroSDK, NitroSystem, MSL_C e DS Protect

Reinventar a roda ao descompilar manualmente funções de sistema da Nintendo ou da biblioteca padrão C da Metrowerks (MSL_C) desperdiça milhares de horas de engenharia. O ecossistema NDS moderno resolve a separação entre código de biblioteca e código de jogo através de três mecanismos complementares que operam em conjunto.

O primeiro mecanismo baseia-se nas assinaturas nativas do dsd (dsd sig). A partir da versão 0.11.0, o dsd incorporou um motor de assinaturas de funções operado pelos comandos dsd sig list, dsd sig apply --config-path config/arm9/config.yaml --all e dsd sig new. Esse subsistema aplica máscaras de padrões de instruções, ignorando offsets variáveis de relocalização, para identificar e renomear instantaneamente funções do NitroSDK como FS_LoadOverlay e FS_UnloadOverlay, preparando inclusive o binário para ferramentas de análise dinâmica no melonDS.

O segundo mecanismo utiliza o harness de cruzamento de bytes em sdk/ combinado com a base de código do projeto pret/pokediamond. Como quase todos os estúdios licenciados compilaram o código-fonte do NitroSDK e do NitroSystem (libnns) com flags padrão do CodeWarrior, as funções compiladas sem alterações produzem sequências de bytes idênticas (módulo relocalizações) entre jogos distintos. No khdays-decomp, o diretório sdk/ compila as fontes do NitroSDK extraídas de pret/pokediamond e compara os objetos gerados contra todas as funções desconhecidas da ROM, catalogando as correspondências exatas e movendo-as para libs/nitro/ e libs/nns/. O mesmo princípio foi expandido em khrecoded-decomp através do script tools/port_days.py, que compila cada função do khdays-decomp, verifica a paridade binária contra a ROM do Re:coded, remapeia todos os símbolos referenciados para os novos endereços utilizados na segunda ROM e importa automaticamente o ficheiro .c apenas se tools/verify_idx.py confirmar uma correspondência byte-exata.

O terceiro mecanismo trata da desencriptação e isolamento da biblioteca antipirataria DS Protect através de taxicat1/dsprot e dsd-ghidra. Jogos da Square Enix e da Nintendo frequentemente incluem o DS Protect em módulos dedicados, como ocorre no ov028 de 358/2 Days ou no ov104 de Re:coded (que utiliza especificamente o DS Protect v1.28 nos lançamentos BK9E, BK9J e BK9P). Essas rotinas aplicam ofuscação e encriptação em tempo de execução sobre funções de verificação de cartucho. O carregador AetiasHax/dsd-ghidra (atualizado com o dsd 0.12.0+) desencripta automaticamente as funções do DS Protect durante a importação, e as suas implementações e nomes de funções são validados diretamente contra a branch 1.28 do repositório taxicat1/dsprot, evitando que o agente tente descompilar código ofuscado ou compilado com versões antigas do compilador sem o devido override em config/arm9/file_compilers.json.

### Compiladores Metrowerks CodeWarrior (mwccarm.exe), Flags e Descoberta Heurística

A geração de código para o processador ARM946E-S em jogos comerciais de Nintendo DS é dominada pelo compilador Metrowerks CodeWarrior for Nintendo DS (mwccarm.exe) e pelo seu linker mwldarm.exe. No caso específico de Kingdom Hearts 358/2 Days e Kingdom Hearts Re:coded, o compilador exato é o mwccarm 3.0 build 139, distribuído oficialmente como parte do pacote CodeWarrior for Nintendo DS 2.0 SP2 Patch 4 (2.0sp2p4), armazenado localmente em tools/mwccarm/3.0_patch4/.

Entretanto, a família de compiladores DS da Metrowerks/Freescale abrange 24 builds históricas distintas, desde a série 1.2/base (2004 build 56) e 1.2/sp2p3 até a série 2.0/base, 2.0/sp1, 2.0/sp2p4 e a série dsi/1.1 (Freescale v4.0 build 1018 de novembro de 2008). Devido a incompatibilidades na versão da EABI (Embedded Application Binary Interface) nos objetos gerados pelo mwccarm.exe, não é possível usar o GNU ld para realizar o link final entre objetos compilados pelo CodeWarrior e objetos gerados pelo GNU binutils; o link final exige obrigatoriamente o mwldarm.exe guiado por ficheiros LCF (Linker Command File) gerados via dsd lcf, enquanto o arm-none-eabi-as (ou mwasmarm.exe -proc arm5te) é utilizado estritamente para montar rotinas .s isoladas.

A tabela abaixo sintetiza as flags de compilação e linking do mwccarm.exe e mwldarm.exe e o impacto direto de cada uma na geração de Assembly ARMv5TE/Thumb:

| Categoria | Flag do Compilador / Linker | Efeito no Gerador de Código (mwccarm / mwldarm) e Impacto no Matching |
|---|---|---|
| Otimização e Escalamento | `-O4,p` vs. `-O4,s` vs. `-O4` | Nível máximo de otimização (4). O sufixo ,p ativa a otimização peephole focada em velocidade/performance, alterando o formato de jump tables (switch) para tabelas indexadas por halfword (16 bits), enquanto ,s otimiza para tamanho. |
| Arquitetura Alvo | `-proc arm946e` (C/C++) / `-proc arm5te` (ASM) / `-proc v5te` (LD) | Instrui o escalonador de instruções sobre o pipeline de 5 estágios do ARM946E-S e habilita instruções da extensão DSP v5TE (como clz, qadd, smulwb, ldrd/strd). |
| Conjunto de Instruções | `-thumb` / `-arm` + `-interworking` | Define o modo padrão da unidade de tradução (Thumb 16-bit ou ARM 32-bit). Funções individuais podem alternar dentro do mesmo ficheiro usando #pragma thumb on e #pragma thumb off. -interworking gera prólogos/epílogos compatíveis com transições BX/BLX. |
| Representação de Tipos | `-enum int` vs. `-enum min` + `-char signed` | -enum int força todos os tipos enum a ocuparem 32 bits com sinal, evitando truncamentos LSL #24 / ASR #24 ou LSL #16 / LSR #16 que ocorrem com -enum min. -char signed define char padrão como s8. |
| Inlining e IPA | `-inline auto` / `-inline noauto` / `-ipa file` | Controla a expansão automática de funções pequenas. -ipa file ativa a análise interprocedimental no escopo do ficheiro, alterando a ordem de emissão de funções e a partilha de literal pools. |
| Dialeto, FPU e Exceções | `-lang c99` (ou `-lang c++`) `-fp soft` `-Cpp_exceptions off` `-gccext,on` | -fp soft garante que toda aritmética de ponto flutuante invoque as rotinas de software do runtime (_fadd, _fmul, _fdiv), pois o ARM946E-S não possui FPU de hardware. -Cpp_exceptions off suprime tabelas de unwinding desnecessárias. |
| Flags do Linker (mwldarm) | `-map -sym on -nodead -w off -proc v5te -interworking -symtab -m _start -msgstyle gcc` | Preserva símbolos (-sym on), impede o dead-stripping de funções ainda não referenciadas diretamente (-nodead), define o ponto de entrada _start e gera o mapa de memória para auditoria do dsd check symbols. |

Quando se inicia a engenharia reversa de uma nova ROM de Nintendo DS sem metadados prévios, o protocolo determinístico validado pela comunidade (exemplificado no estudo de Fossil Fighters Champions — arcnid/ffc-decomp) combina heurística temporal de SDK com uma varredura automatizada das 24 versões do compilador. Inicialmente, inspecionam-se strings da ROM e a estrutura do arm9.bin para datar o SDK: jogos de 2004–2005 (como Super Mario 64 DS e Animal Crossing: Wild World) utilizam tipicamente mwccarm 1.2/base (2004 build 56) ou 1.2/sp2p3; jogos de 2006–2009 (como Pokémon Diamond/Pearl e KH 358/2 Days) concentram-se em 2.0/base, 2.0/sp1 ou 2.0/sp2p4 (3.0 build 139); já títulos tardios de 2009–2011 ou híbridos DSi (como Fossil Fighters Champions e Pokémon Black/White 2) utilizam dsi/1.1 (Freescale v4.0 build 1018). Em seguida, baixa-se o pacote completo com as 24 versões históricas do mwccarm disponibilizado nos lançamentos do decomp.me, seleciona-se uma função de complexidade média (preferencialmente contendo um switch/case e acessos a membros de struct) e executa-se um script Python que compila o código C candidato nas 24 versões combinadas com a matriz de flags (-O4,p vs -O4, -enum int vs -enum min, -inline auto vs -inline off). A versão e o conjunto de flags que zerarem o diff de bytes (ignorando bits de relocalização) identificam de forma inequívoca a toolchain original do estúdio.

### Integração em Loop Fechado: dsd, objdiff (objdiff.json) e decomp.me

Para que um agente de IA como o Claude Code atinja 100% de paridade de bytes sem depender da interface gráfica do objdiff, a toolchain articula a geração automática de configuração pelo dsd, a execução headless do objdiff-cli e a criação de scratches para o decomp.me. O subcomando dsd objdiff lê o config/arm9/config.yaml e gera automaticamente o ficheiro objdiff.json na raiz do projeto:

```bash
tools/dsd objdiff \
  --config-path config/arm9/config.yaml \
  --output-path . \
  --scratch \
  --c-flags "-O4,p -enum int -proc arm946e -gccext,on -fp soft -lang c99 -Cpp_exceptions off -interworking" \
  --custom-make ninja
```

O objdiff.json gerado mapeia cada unidade (units) associando o target_path (o objeto .o extraído e separado da ROM original pelo dsd delink em build/delinks/...) ao base_path (o objeto .o compilado pelo mwccarm.exe a partir do código C atual do agente). Em vez de abrir janelas gráficas, o Claude Code invoca o binário objdiff-cli diretamente no terminal Linux/WSL2. Para auditar o progresso global e gerar relatórios JSON padronizados compatíveis com decomp.dev e bytematch.dev, executa-se objdiff-cli report generate; já para inspecionar o diff exato de instruções e registos de um símbolo específico no terminal — aplicando as mesmas regras estritas de pontuação name_check do objdiff.json que acusam erro até mesmo se uma chamada BL apontar para o símbolo errado com bytes idênticos — utiliza-se o subcomando diff:

```bash
# Geração de relatório estruturado de progresso
objdiff-cli report generate -p . -o build/report.json -f json

# Inspeção headless do diff de uma função individual pelo agente
objdiff-cli diff -p . -u <nome_da_unidade> <nome_da_funcao>
```

Quando uma função apresenta anomalias severas de alocação de registos (regalloc) ou ordenação de pilha, o script python tools/decompme.py func_XXXXXXXX (ou a flag --scratch do dsd objdiff) empacota os cabeçalhos pré-processados (context), o Assembly alvo, as flags e o preset do compilador (mwccarm 3.0 build 139), permitindo criar ou consultar scratches reprodutíveis na plataforma decomp.me.

## Módulo 2: Como Conectar o Ghidra + Nintendo DS Loader ao Servidor MCP do REA

### Análise Comparativa dos Carregadores de Nintendo DS para Ghidra 12.1.x

O servidor MCP do REA (rea-agents v6.5.0 / v6.7.0) exige estritamente o Ghidra 12.1.x (12.1.2 ou 12.1.4) executado sobre JDK 21 (64-bit). Por padrão, a instalação limpa do Ghidra reconhece a arquitetura de processador do Nintendo DS (ARM:LE:32:v5t para o ARM946E-S e ARM:LE:32:v4t para o ARM7TDMI), mas não possui um carregador (Loader) nativo capaz de interpretar o formato de contentor de cartucho .nds, descomprimir binários comprimidos com Backwards LZ (BLZ) ou mapear tabelas de overlays (y9.bin).

A avaliação técnica das extensões disponíveis para Ghidra no ecossistema NDS distingue claramente duas ferramentas. O GhidraNintendoDSLoader (pedro-javierf / NDSLoader) é um carregador em Java puro que lê o cabeçalho header.bin da ROM .nds, descomprime o arm9.bin, o arm7.bin e os overlays, e cria blocos de memória no Ghidra; contudo, depende do analisador automático genérico do Ghidra (que frequentemente falha na deteção de limites entre funções ARM e Thumb ou interpreta jump tables e literal pools como código executável) e não possui integração com os ficheiros symbols.txt, delinks.txt e relocs.txt do dsd. Em contrapartida, o AetiasHax/dsd-ghidra constitui o padrão ouro para projetos baseados em dsd. Desenvolvido pelo mesmo autor do dsd, o dsd-ghidra é uma extensão híbrida composta por 76,5% de código Java e 22,6% de código Rust compilado como biblioteca nativa JNI com o motor dsd 0.12.0+. Ele carrega a ROM no Ghidra utilizando diretamente os algoritmos do dsd, desencripta automaticamente módulos protegidos com DS Protect e inclui o script essencial SyncDsd.java.

### Resolução do Conflito de Endereços de Overlays no open_binary do REA

Um dos maiores obstáculos arquiteturais ao conectar um servidor MCP como o REA a um jogo de Nintendo DS reside na topologia física e virtual da memória RAM do console. No mapa de memória do ARM9, além da memória rápida ITCM (0x01FF8000, 32 KB) e DTCM (0x027E0000, 16 KB), a Main RAM de 4 MB (0x02000000 a 0x023FFFFF) aloja o binário estático arm9.bin na base (ex: 0x02000000 a 0x0213FFFF), enquanto as dezenas ou centenas de overlays dinâmicos (como os 306 módulos do KH 358/2 Days ou os 105 overlays do KH Re:coded) são compilados para executar exatamente nas mesmas janelas de endereço virtual sobrepostas (por exemplo, overlay_0001 e overlay_0002 ambos mapeados em 0x02140000).

Passar uma ROM .nds bruta diretamente para a ferramenta open_binary do REA no modo headless do Ghidra provoca três falhas críticas: o Ghidra Headless padrão não consegue desambiguar qual overlay está ativo em 0x02140000 durante a resolução de referências cruzadas (xrefs); a análise simultânea de mais de 300 overlays num único banco de dados na inicialização excede a janela de timeout (REA_GHIDRA_STARTUP_TIMEOUT_MS=120000) e o limite de heap JVM (--max-heap-mb 1024); e, conforme alerta explicitamente a documentação oficial do dsd-ghidra, executar o Auto-Analyze padrão do Ghidra sobre uma ROM de DS recém-importada degrada severamente a precisão da análise de funções, pois o analisador genérico tenta adivinhar modos ARM/Thumb sem consultar as tabelas pré-calculadas pelo dsd.

A arquitetura híbrida Windows 11 + WSL2 Ubuntu 24.04 resolve esse conflito desacoplando o mapa de memória do ARM9 em duas vias de ingestão para o servidor REA MCP (configurado com o perfil compact sobre Ghidra 12.1.4 e JDK 21 no WSL2) enquanto mantém a execução de compilação (mwccarm.exe) e depuração (melonDS GDB Stub) no host nativo Windows 11:

| Parâmetro Arquitetural | Estratégia 1: Ingestão de ELFs Relocalizáveis por Módulo (dsd delink) | Estratégia 2: Snapshot Global Pré-Sincronizado (dsd-ghidra + SyncDsd.java) |
|---|---|---|
| Artefato Fornecido ao open_binary | `path: "/workspace/build/delinks/<modulo>/<unidade>.o"` com `provider_id: "ghidra"`. | `snapshot_path: "/workspace/ghidra_snapshots/game_synced.gzf"` (ou projeto Ghidra persistido via binary_session). |
| Mecanismo de Isolamento de Memória | O dsd delink (ou o partial linking via dsd lcf + dsd json delinks) extrai cada unidade de tradução ou overlay como um ficheiro objeto ELF (.o) independente. | O dsd-ghidra instancia o arm9.bin na RAM base e aloca cada overlay no seu próprio MemoryBlock (Overlay Space, ex: ov000::02140000, ov001::02140000), evitando colisões físicas. |
| Preservação de Símbolos e Modo ARM/Thumb | A tabela .symtab e .rel.text do ELF já incorpora todos os nomes de symbols.txt, marcas de modo $a (ARM) / $t (Thumb) e alvos de relocalização. | O script SyncDsd.java (executado sem o Auto-Analyze padrão do Ghidra) injeta funções, tamanhos, modos ARM/Thumb e relocalizações diretamente do config.yaml. |
| Tempo de Carga no REA Headless | Instantâneo (1 a 3 segundos por módulo de poucos kilobytes), consumindo fração mínima do limite --max-heap-mb 1024. | Rápido após a criação inicial do snapshot, pois o REA importa o estado já analisado sem reexecutar os analisadores de código. |
| Caso de Uso Ideal para o Claude Code | Loop fechado de descompilação correspondente (Via A) focado numa função ou overlay específico (decompile e instructions). | Rastreio de referências cruzadas globais inter-módulos (xrefs, search) e auditoria de pontos de injeção para Modding Rápido (Via B). |

### Sincronização Bidirecional Automática de Símbolos: symbols.txt ↔ Ghidra ↔ REA

Manter a coerência semântica entre as descobertas do agente no REA/Ghidra e os ficheiros de configuração do dsd exige um protocolo bidirecional estrito. No sentido dsd para Ghidra/REA, sempre que os ficheiros config/**/symbols.txt ou relocs.txt são atualizados por scripts externos (como dsd sig apply --all, tools/port_days.py ou edições diretas do Claude Code nos ficheiros de configuração), a sincronização para o banco de dados do Ghidra ocorre executando o script SyncDsd.java no Ghidra ou regenerando os objetos ELF com dsd delink --config-path config/arm9/config.yaml, o que atualiza imediatamente a tabela .symtab lida pelo REA nos objetos de build/delinks/.

No sentido inverso (REA/Ghidra e código C para dsd), quando o Claude Code descobre o propósito de uma função ou variável durante uma sessão do REA, ele utiliza as ferramentas MCP set_address_name ou set_addresses_names, que funcionam nativamente com o provider Ghidra do REA para renomear entradas de funções e rótulos primários de dados, persistindo o snapshot de forma atômica através do parâmetro snapshot_path e overwrite: true em close_binary e acionando o SyncDsd.java para gravar os novos nomes em config/**/symbols.txt. Paralelamente, quando o agente renomeia funções ou variáveis diretamente no código-fonte C/C++ e realiza o link do executável ELF (final_link.o) via mwldarm.exe, o subcomando nativo dsd apply propaga automaticamente os dados de símbolos do ELF compilado de volta para todos os ficheiros symbols.txt do projeto, utilizando correspondência fuzzy por padrão para impedir que símbolos internos gerados pelo compilador (como @1234) sobrescrevam nomes válidos:

```bash
# Propaga nomes e metadados do ELF linkado para os symbols.txt (com filtro fuzzy padrão contra símbolos internos @1234)
tools/dsd apply --config-path config/arm9/config.yaml --elf-path build/arm9/final_link.o

# Reordena deterministicamente todos os symbols.txt, delinks.txt e relocs.txt por endereço preservando comentários //
tools/dsd format --config-path config/arm9/config.yaml
```

## Módulo 3: Duas Vias de Trabalho — (A) Descompilação Completa vs. (B) Modding Rápido e ROM Hacking

A engenharia reversa de Nintendo DS divide-se em dois paradigmas operacionais distintos: a Via A (Descompilação Correspondente), cujo objetivo é reconstruir 100% do código-fonte C/C++ original para documentação científica, mods profundos de motor ou criação de ports nativos para PC e PlayStation Vita; e a Via B (Modding Rápido e Injeção de Código), projetada para alterar mecânicas, criar novos atores, modificar recursos gráficos/sonoros no NitroFS e injetar código C++20 moderno em poucos minutos sem precisar descompilar a ROM inteira.

### Via A: Descompilação Correspondente (Matching Decomp / Port Nativo)

Na Via A, o contrato de engenharia exige que o código C/C++ escrito pelo agente produza exatamente os mesmos bytes de código de máquina da ROM comercial quando compilado pelo mwccarm.exe. O fluxo operacional completo desenvolve-se em cinco estágios sequenciais.

No primeiro estágio, realiza-se a seleção do alvo e a extração de evidência estática combinando o dsd com o REA. O agente identifica uma função ainda não descompilada em config/arm9/**/symbols.txt ou executa python3 tools/getcand.py func_XXXXXXXX. Em seguida, abre o ELF relocalizável correspondente em build/delinks/ utilizando open_binary no servidor MCP do REA e invoca decompile (para obter o pseudocódigo estruturado do Ghidra), instructions (para ler o Assembly bruto com os offsets exatos e literal pools) e xrefs (para identificar os tipos dos argumentos passados pelos chamadores e o layout das structs acedidas).

No segundo estágio, ocorre a tradução idiomática para C compatível com o CodeWarrior (mwccarm) sob o dialeto C99 (-lang c99). O agente verifica o atributo de modo em symbols.txt: se for mode:thumb, a função deve ser compilada sob #pragma thumb on (ou com a flag global -thumb); se for mode:arm, utiliza-se #pragma thumb off. Como o ARM946E-S não possui FPU de hardware (-fp soft), toda a matemática 3D, física e de matrizes do NitroSDK utiliza inteiros de 32 bits em formato de ponto fixo Q19.12 (typedef s32 fx32;, onde FX32_ONE = 0x1000 = 4096). Além disso, sob -O4,p, a ordem de declaração de variáveis locais no topo do bloco C governa diretamente a atribuição dos registos preservados (r4–r11 em ARM, r4–r7 em Thumb), permitindo resolver divergências de regalloc reordenando declarações ou introduzindo variáveis temporárias explícitas.

No terceiro estágio, executa-se a compilação unitária rápida via interoperabilidade do WSL2 com o mwccarm.exe. A partir do ambiente Linux no WSL2, o sistema de build (ninja ou tools/match.py) invoca diretamente o binário tools/mwccarm/3.0_patch4/mwccarm.exe nativo do Windows (ou através do loader Win32 ultraleve wibo), exportando previamente a variável LM_LICENSE_FILE apontando para o ficheiro license.dat do CodeWarrior.

No quarto estágio, processa-se a verificação headless de paridade de bytes com objdiff-cli ou match.py. O agente executa objdiff-cli diff -p . -u <unidade> <funcao> (ou python3 tools/match.py <ficheiro.c>) e analisa a saída textual no terminal. Diferenças de instruções, desvios de offset de pilha (sp) ou desalinhamento de literal pools são corrigidos iterativamente até que a diferença seja zero (>>> MATCH <<< / 100%).

No quinto estágio, realiza-se a validação de linkagem global e o reempacotamento da ROM com dsd check e dsd rom build. Quando a função ou o módulo atinge 100%, o ficheiro é integrado em delinks.txt, o linker mwldarm.exe gera o ELF final com o script LCF produzido por dsd lcf, e os validadores de integridade verificam cada módulo e símbolo antes de empacotar a ROM .nds final:

```bash
# Verifica se todos os módulos compilados batem byte-a-byte com os originais
tools/dsd check modules --config-path config/arm9/config.yaml --fail

# Verifica se todos os símbolos de symbols.txt existem nos endereços exatos do ELF final
tools/dsd check symbols --config-path config/arm9/config.yaml --elf-path build/arm9/final_link.o --fail

# Reconstrói a ROM .nds final e compara o hash SHA-1 contra a ROM de referência
tools/dsd rom build --config dsd_extract/config.yaml --rom build/rebuilt.nds
```

### Via B: Modding Rápido e Injeção de Código C/C++/ASM (Sem Descompilar o Jogo Inteiro)

Quando o objetivo é criar um mod funcional — como adicionar um sistema de câmara livre, alterar fórmulas de precisão de batalha em tempo real, criar novos atores ou expandir tabelas de itens — descompilar 23.000 funções é desnecessário. A Via B utiliza a stack moderna liderada pelo NCPatcher (TheGameratorT/NCPatcher), Fireflower (MammaMiaTeam/Fireflower), neondst (pants64DS/neondst), ndspy, devkitARM (arm-none-eabi-gcc) e armips.

O NCPatcher opera sob o princípio de separação estrita de responsabilidades: ele modifica exclusivamente os binários executáveis (arm9.bin, arm7.bin e overlays) dentro de uma árvore extraída, delegando o desempacotamento e reempacotamento da imagem .nds para utilitários externos. No fluxo standalone, utilizam-se as ferramentas em C++20 nds-extract e nds-build (distribuídas com o Fireflower e integradas também no gestor incremental neondst), que extraem a ROM mantendo um manifesto buildrules.txt e reconstroem o .nds recalculando os CRCs do cabeçalho. Alternativamente, scripts Python baseados na biblioteca ndspy (ou o clássico ndstool) permitem ler, injetar e reempacotar binários e ficheiros do NitroFS diretamente em pipelines automatizados sem intervenção de editores gráficos.

Ao contrário da Via A (que exige o compilador proprietário mwccarm.exe), o NCPatcher utiliza o Arm GNU Toolchain (arm-none-eabi-gcc / g++) moderno, permitindo escrever mods utilizando recursos completos de C++20 (-std=c++20). O projeto é governado por um ficheiro raiz ncpatcher.json que aponta para os ficheiros de configuração de alvo (como arm9.json e arm7.json):

```json
{
  "$arm_flags": "-masm-syntax-unified -mno-unaligned-access -mfloat-abi=soft -mabi=aapcs",
  "$c_flags": "-Os -fomit-frame-pointer -ffast-math -fno-builtin -nostdlib -nodefaultlibs -nostartfiles -DSDK_GCC -DSDK_FINALROM",
  "$cpp_flags": "-fno-rtti -fno-exceptions -std=c++20",
  "$asm_flags": "-Os -x assembler-with-cpp",
  "backup": "backup",
  "filesystem": "extracted_rom",
  "toolchain": "arm-none-eabi-",
  "arm9": {
    "target": "arm9.json",
    "build": "build/arm9"
  },
  "thread-count": 0
}
```

Dentro do ficheiro de alvo (arm9.json), o NCPatcher disponibiliza três modos de inserção (mode) para alojar o código novo compilado pelo GCC: o modo "append" anexa o novo código ao final de um binário existente (sendo a única opção para o módulo "main" / arm9.bin); o modo "replace" apaga todo o conteúdo de um overlay existente e posiciona o código customizado no seu lugar (permitindo definir um novo endereço base e aplicar compressão Backwards LZ via "compress": true); e o modo "create" gera um overlay totalmente novo na ROM.

Para desviar o fluxo de execução do jogo original para o código C/C++ injetado, o NCPatcher oferece macros de patching estático (aplicadas diretamente ao binário em tempo de compilação) e dinâmico (armazenadas desativadas e aplicadas em tempo de execução):

| Macro / Atributo do NCPatcher | Mecanismo de Geração de Código | Restrições de Modo (ARM vs. THUMB) e Footprint de Sobrescrita |
|---|---|---|
| `ncp_jump(addr, [ov])` / `ncp_tjump(addr, [ov])` | Substitui a instrução em addr por um salto incondicional (B) direto para a função customizada. | A partir de ARM (ncp_jump), sobrescreve 1 instrução (4 bytes). A partir de THUMB (ncp_tjump ou addr+1), sobrescreve 4 ou 8 bytes (2 a 4 instruções). |
| `ncp_call(addr, [ov])` / `ncp_tcall(addr, [ov])` | Substitui a instrução em addr por uma chamada de sub-rotina (BL / BLX) para a função customizada. | A partir de ARM (ncp_call), sobrescreve 1 instrução (4 bytes). A partir de THUMB (ncp_tcall ou addr+1), sobrescreve 4 ou 8 bytes (2 a 4 instruções). |
| `ncp_hook(addr, [ov])` | Substitui a instrução em destAddr por um salto para uma ponte gerada (hook_bridge) que preserva R0–R3 e R12, chama srcAddr (BL ou BLX), restaura os registos e retorna a destAddr + 4. | Suportado exclusivamente a partir de código em modo ARM (sobrescreve 4 bytes de forma 100% segura). O alvo srcAddr pode ser ARM ou THUMB. |
| `ncp_repl(addr, [ov], "asm")` e `ncp_over(addr, [ov])` | Substitui instruções específicas inline ou sobrescreve blocos de código/tabelas de dados existentes. | O tamanho sobrescrito corresponde exatamente às instruções ou dados declarados pelo programador. |

A ponte gerada automaticamente pelo NCPatcher para o tipo ncp_hook segue a estrutura exata abaixo:

```asm
hook_bridge:
    PUSH {R0-R3, R12}
    BL   srcAddr        @ Usa BLX se srcAddr foi compilado em modo THUMB
    POP  {R0-R3, R12}
    <instrucao_original_de_destAddr>
    B    (destAddr + 4)
```

Essa assimetria física impõe uma regra de ouro para o Claude Code: enquanto qualquer hook feito a partir do modo ARM sobrescreve sempre uma única instrução de 4 bytes de forma segura, colocar um ncp_tjump ou ncp_tcall sobre código THUMB sobrescreve obrigatoriamente 4 ou 8 bytes (2 a 4 instruções THUMB de 2 bytes). Assim, antes de aplicar um hook num endereço THUMB descoberto no REA, o agente deve obrigatoriamente executar rea instructions na função alvo para garantir que os 4 ou 8 bytes a partir do endereço de destino não partam ao meio uma instrução BL de 4 bytes, não destruam o alvo de um salto condicional curto nem invalidem uma carga LDR rX, [PC, #imm].

Para micro-patches em Assembly puro (padrão consagrado em mods de Pokémon HeartGold/SoulSilver e Platinum), utiliza-se o assembler armips (de Kingcom), que abre os binários descomprimidos, posiciona o cursor por endereço virtual e monta instruções ARM/Thumb diretamente. Já a modificação de assets na partição NitroFS (data/) articula a biblioteca Python ndspy.narc ou o utilitário knarc para desempacotar e reempacotar arquivos NARC (Nitro Archive), o nitrogfx para converter gráficos 2D (NCGR, NCLR, NSCR) de/para .png, e o apicula para converter modelos e texturas 3D (NSBMD, NSBTX). Quando a análise estática precisa de validação em tempo de execução, o agente aciona o melonDS com o GDB Stub ativado (expondo a porta 3333 para o ARM9) e conecta o gdb-multiarch em modo batch a partir do WSL2 para inspecionar registos e memória viva:

```bash
gdb-multiarch -batch \
  -ex "set architecture armv5te" \
  -ex "target remote $(ip route show | awk '/default/ {print $3}'):3333" \
  -ex "x/16xw 0x02140000" \
  -ex "info registers" \
  -ex "detach"
```

## Módulo 4: Guia Passo a Passo de Provisionamento Windows 11 + WSL2

Este roteiro de provisionamento é 100% automatizável e executável pelo Claude Code. A topologia instala os motores de análise estática (Ghidra 12.1.4, JDK 21, dsd-ghidra, rea-agents, dsd, objdiff-cli, NCPatcher, arm-none-eabi-gcc) dentro do WSL2 (Ubuntu 24.04) — contornando a restrição experimental P0 do provider Ghidra no Windows nativo e garantindo a presença do utilitário ps (procps) exigido pelo REA — enquanto preserva a invocação direta dos binários nativos do Windows (mwccarm.exe, mwldarm.exe, melonDS.exe, objdiff.exe) através da ponte de interoperabilidade do WSL2.

### Etapa 1: Preparação do Host Windows 11 (PowerShell como Administrador)

O bloco PowerShell abaixo ativa o WSL2, instala o Ubuntu 24.04 LTS e cria a estrutura de diretórios partilhada para os emuladores e ferramentas gráficas no Windows:

```powershell
# Executar no PowerShell (Administrador) no Windows 11
$ErrorActionPreference = "Stop"

# 1. Garantir que o WSL2 e o Ubuntu 24.04 estejam instalados
wsl --install -d Ubuntu-24.04 --no-launch
wsl --set-default-version 2

# 2. Criar diretório de ferramentas nativas Windows acessível via /mnt/c/nds-tools no WSL2
$WinToolsDir = "C:\nds-tools"
New-Item -ItemType Directory -Force -Path "$WinToolsDir\melonDS" | Out-Null
New-Item -ItemType Directory -Force -Path "$WinToolsDir\objdiff-gui" | Out-Null

# 3. Baixar objdiff GUI (Windows x64)
Invoke-WebRequest -Uri "https://github.com/encounter/objdiff/releases/latest/download/objdiff-windows-x86_64.exe" -OutFile "$WinToolsDir\objdiff-gui\objdiff.exe"

Write-Host "Host Windows 11 preparado. Inicie a instância Ubuntu-24.04 para executar o provisionamento Linux."
```

### Etapa 2: Dependências Base Linux, JDK 21 e Devkit/GNU ARM (WSL2 Ubuntu 24.04)

Dentro do terminal WSL2 (Ubuntu 24.04), este script instala o pacote obrigatório procps (para verificação de propriedade de processos pelo REA), o OpenJDK 21 de 64 bits, o Gradle, a toolchain arm-none-eabi e as bibliotecas Python de engenharia reversa (capstone, pyelftools, ndspy, pycparser):

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Atualizar repositórios e instalar dependências de sistema + procps (obrigatório para o REA) + JDK 21
sudo apt-get update
sudo apt-get install -y \
  procps build-essential pkg-config libssl-dev \
  python3 python3-pip python3-venv \
  ninja-build cmake git unzip curl wget jq \
  openjdk-21-jdk gradle \
  gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
  gdb-multiarch

# 2. Verificar presença do procps (ps) e configurar JAVA_HOME estático para JDK 21 (64-bit)
which ps
ps -ef | head -n 5

export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
if ! grep -q "JAVA_HOME" ~/.bashrc; then
  echo 'export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"' >> ~/.bashrc
  echo 'export PATH="$JAVA_HOME/bin:$HOME/.local/bin:$HOME/.cargo/bin:$PATH"' >> ~/.bashrc
fi

# 3. Instalar pacotes Python essenciais (capstone, pyelftools, ndspy, pycparser)
python3 -m pip install --user --break-system-packages \
  capstone pyelftools ndspy pycparser pyyaml
```

### Etapa 3: Instalação do Ghidra 12.1.4 e Compilação do dsd-ghidra via Gradle

O provider Ghidra do REA exige uma versão estrita da família Ghidra 12.1.x (12.1.2 ou 12.1.4) combinada com o Java 21. O script abaixo instala o Ghidra 12.1.4 em /opt/ghidra, instala o Rust (rustup) e compila a extensão AetiasHax/dsd-ghidra (incluindo a biblioteca JNI nativa em Rust via ./build.sh e o pacote de extensão via gradle distributeExtension):

```bash
#!/usr/bin/env bash
set -euo pipefail

export JAVA_HOME="/usr/lib/jvm/java-21-openjdk-amd64"
export GHIDRA_INSTALL_DIR="/opt/ghidra"

# 1. Instalar Rust via rustup (necessário para dsd, dsd-ghidra e objdiff-cli)
if ! command -v cargo &> /dev/null; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  source "$HOME/.cargo/env"
fi

# 2. Baixar e instalar o Ghidra 12.1.4 (ou 12.1.2) em /opt/ghidra
mkdir -p /tmp/ghidra-dl
cd /tmp/ghidra-dl
GHIDRA_URL=$(curl -s https://api.github.com/repos/NationalSecurityAgency/ghidra/releases | jq -r '.[] | select(.tag_name | test("Ghidra_12\\.1\\.(4|2)")) | .assets[0].browser_download_url' | head -n 1)
wget -O ghidra.zip "$GHIDRA_URL"
sudo rm -rf "$GHIDRA_INSTALL_DIR"
sudo mkdir -p "$GHIDRA_INSTALL_DIR"
sudo unzip -q ghidra.zip -d /opt/ghidra-tmp
sudo mv /opt/ghidra-tmp/ghidra_*/* "$GHIDRA_INSTALL_DIR/"
sudo rm -rf /opt/ghidra-tmp ghidra.zip

if ! grep -q "GHIDRA_INSTALL_DIR" ~/.bashrc; then
  echo 'export GHIDRA_INSTALL_DIR="/opt/ghidra"' >> ~/.bashrc
fi

# 3. Clonar e compilar o AetiasHax/dsd-ghidra (Rust JNI + Extensão Java Gradle)
mkdir -p "$HOME/toolchains-src"
cd "$HOME/toolchains-src"
rm -rf dsd-ghidra
git clone https://github.com/AetiasHax/dsd-ghidra.git
cd dsd-ghidra

# Compilar a biblioteca nativa Rust (libdsd_ghidra.so)
chmod +x build.sh
./build.sh

# Configurar gradle.properties apontando para /opt/ghidra e gerar o .zip da extensão
cp dsd-ghidra/gradle.properties.example dsd-ghidra/gradle.properties
sed -i "s|^GHIDRA_INSTALL_DIR=.*|GHIDRA_INSTALL_DIR=/opt/ghidra|g" dsd-ghidra/gradle.properties

cd dsd-ghidra
gradle distributeExtension

# Instalar a extensão compilada diretamente no diretório de extensões do Ghidra 12.1.x
sudo unzip -o dist/*.zip -d "$GHIDRA_INSTALL_DIR/Ghidra/Extensions/"

# Copiar também o SyncDsd.java para ghidra_scripts global do utilizador para fácil acesso headless
mkdir -p "$HOME/ghidra_scripts"
find . -name "SyncDsd.java" -exec cp {} "$HOME/ghidra_scripts/" \;
```

### Etapa 4: Compilação do dsd, objdiff-cli, NCPatcher, Fireflower, armips e wibo

Este bloco instala as ferramentas de linha de comando em ~/.cargo/bin e ~/.local/bin para que tanto a Via A quanto a Via B estejam imediatamente disponíveis no PATH do WSL2:

```bash
#!/usr/bin/env bash
set -euo pipefail
source "$HOME/.cargo/env"
mkdir -p "$HOME/.local/bin" "$HOME/toolchains-src"

# 1. Instalar dsd (AetiasHax/ds-decomp v0.12.1+) e objdiff-cli (encounter/objdiff) via Cargo
cargo install --locked --git https://github.com/AetiasHax/ds-decomp.git --bin dsd
cargo install --locked --git https://github.com/encounter/objdiff.git objdiff-cli

# 2. Compilar NCPatcher (TheGameratorT/NCPatcher) no Linux
cd "$HOME/toolchains-src"
rm -rf NCPatcher
git clone --recursive https://github.com/TheGameratorT/NCPatcher.git
cd NCPatcher && mkdir build && cd build
cmake ../ -DCMAKE_BUILD_TYPE=Release
make -j"$(nproc)"
cp ncpatcher "$HOME/.local/bin/"

# 3. Compilar nds-extract e nds-build (MammaMiaTeam/Fireflower) em C++20
cd "$HOME/toolchains-src"
rm -rf Fireflower
git clone https://github.com/MammaMiaTeam/Fireflower.git
cd Fireflower
if [ -f "nds-extract.cpp" ]; then
  g++ nds-extract.cpp -o "$HOME/.local/bin/nds-extract" -std=c++20 -O2
  g++ nds-build.cpp -o "$HOME/.local/bin/nds-build" -std=c++20 -O2
fi

# 4. Compilar armips (Kingcom/armips) para injeção de Assembly
cd "$HOME/toolchains-src"
rm -rf armips
git clone --recursive https://github.com/Kingcom/armips.git
cd armips && mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
make -j"$(nproc)"
cp armips "$HOME/.local/bin/"

# 5. Instalar wibo (loader Win32 leve para Linux, alternativa ao binfmt Windows nativo)
cd "$HOME/toolchains-src"
wget -O "$HOME/.local/bin/wibo" "https://github.com/decompals/wibo/releases/latest/download/wibo-x86_64"
chmod +x "$HOME/.local/bin/wibo"
```

### Etapa 5: Configuração do Compilador Metrowerks (mwccarm.exe) e Interoperabilidade WSL2

O mwccarm.exe e o mwldarm.exe são executáveis Windows de 32 bits. No WSL2, eles podem ser invocados diretamente via interoperabilidade nativa do kernel (binfmt_misc) ou através do wrapper wibo. O script abaixo organiza o diretório de compiladores mwccarm (incluindo o 3.0_patch4 correspondente ao CodeWarrior 2.0 SP2p4 / build 139 utilizado no khdays-decomp) e configura a variável LM_LICENSE_FILE:

```bash
#!/usr/bin/env bash
set -euo pipefail

MWCC_ROOT="$HOME/.local/share/mwccarm"
mkdir -p "$MWCC_ROOT"
cd "$MWCC_ROOT"

# Baixar o arquivo consolidado de compiladores mwccarm utilizado pelo ecossistema decomp.me / pret
wget -O mwccarm_pack.zip "https://github.com/pret/pokediamond/raw/master/tools/mwccarm/mwccarm.zip" || true
if [ -f "mwccarm_pack.zip" ]; then
  unzip -o -q mwccarm_pack.zip
  rm mwccarm_pack.zip
fi

# Criar alias para 3.0_patch4 (CodeWarrior 2.0 SP2p4 - mwccarm 3.0 build 139 usado no khdays-decomp)
mkdir -p "$MWCC_ROOT/3.0_patch4"
if [ -d "$MWCC_ROOT/2.0/sp2p4" ]; then
  cp -r "$MWCC_ROOT/2.0/sp2p4/"* "$MWCC_ROOT/3.0_patch4/"
fi

# Garantir permissão de execução (+x) para que o WSL2 Interop possa invocar os .exe diretamente
find "$MWCC_ROOT" -name "*.exe" -exec chmod +x {} \;

# Configurar variável de ambiente LM_LICENSE_FILE exigida pelo mwccarm.exe
touch "$MWCC_ROOT/license.dat"
if ! grep -q "LM_LICENSE_FILE" ~/.bashrc; then
  echo "export LM_LICENSE_FILE=\"$MWCC_ROOT/license.dat\"" >> ~/.bashrc
fi
```

### Etapa 6: Node.js 24 LTS, Instalação do rea-agents e Configuração MCP Otimizada no Claude Code

Por fim, instala-se o Node.js 24 LTS (v24.11+), instala-se globalmente o pacote rea-agents (v6.7.0+) e configura-se o servidor MCP do REA para o Claude Code aplicando as otimizações críticas de consumo de contexto ("REA_MCP_INPUT_SCHEMA_PROFILE": "compact", que economiza ~145.000 tokens por sessão ao compactar os esquemas das 207 ferramentas) e os limites de memória/timeout do Ghidra:

```bash
#!/usr/bin/env bash
set -euo pipefail

# 1. Instalar Node.js 24 LTS via NodeSource
curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -
sudo apt-get install -y nodejs

node -v # Deve reportar v24.11.0 ou superior

# 2. Instalar globalmente o REA (morluto/rea -> npm package rea-agents) e a skill oficial
sudo npm install --global rea-agents@latest
npx --yes skills add morluto/rea --skill reverse-engineer-anything || true

# 3. Configurar o servidor MCP do REA no Claude Code com perfil COMPACT e Ghidra Provider
python3 - << 'EOF'
import json, os

claude_cfg_path = os.path.expanduser("~/.claude.json")
cfg = {}
if os.path.exists(claude_cfg_path):
    try:
        with open(claude_cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        cfg = {}

cfg.setdefault("mcpServers", {})
cfg["mcpServers"]["rea"] = {
    "command": "rea",
    "args": [
        "mcp",
        "--max-heap-mb", "1024",
        "--analysis-timeout-ms", "60000"
    ],
    "env": {
        "REA_MCP_INPUT_SCHEMA_PROFILE": "compact",
        "REA_ANALYSIS_PROVIDER": "ghidra",
        "REA_GHIDRA_STARTUP_TIMEOUT_MS": "120000",
        "GHIDRA_INSTALL_DIR": "/opt/ghidra",
        "JAVA_HOME": "/usr/lib/jvm/java-21-openjdk-amd64"
    }
}

with open(claude_cfg_path, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2)
print("Configuração MCP do REA gravada com sucesso em ~/.claude.json")
EOF

# 4. Validar toda a cadeia (Node, procps, Ghidra 12.1.4, JDK 21 e registo do cliente claude_code)
rea doctor --client claude_code --json
```

Caso a arquitetura escolhida mantenha o cliente do agente no Windows 11 nativo e o servidor MCP no WSL2, inicia-se o servidor REA em modo HTTP streamable dentro do WSL2:

```bash
REA_MCP_INPUT_SCHEMA_PROFILE=compact \
REA_ANALYSIS_PROVIDER=ghidra \
REA_GHIDRA_STARTUP_TIMEOUT_MS=120000 \
GHIDRA_INSTALL_DIR=/opt/ghidra \
JAVA_HOME=/usr/lib/jvm/java-21-openjdk-amd64 \
rea mcp --http --port 8765 --max-heap-mb 1024 --analysis-timeout-ms 60000
```

## Módulo 5: Artefatos Prontos para Produção do Agente (CLAUDE.md e SKILL.md)

Os dois blocos a seguir continham a implementação integral dos ficheiros CLAUDE.md (para a raiz do repositório) e `.agents/skills/nds-rea-modder/SKILL.md` (seguindo a especificação aberta de Agent Skills), codificando de forma determinística as regras operacionais para o Claude Code conduzir ciclos autônomos de descompilação correspondente (Via A) e modding rápido (Via B).

> As versões adotadas neste repositório (corrigidas e expandidas) estão em `CLAUDE.md` e em
> `.agents/skills/nds-rea-modder/SKILL.md`. O conteúdo técnico original desses blocos — mapa do
> workspace, comandos do dsd/objdiff/khdays, regras do REA (verificação de sessão, proibição de abrir
> o `.nds` bruto, ELF do dsd delink vs snapshot global, contrato de evidência), alternância ARM/Thumb e
> bit T, literal pools (`(PC & ~3) + 4 + imm` em Thumb, `PC + 8 + imm` em ARM), ponto fixo Q19.12 e
> `FX_Mul` (`SMULL` + `ADDS #0x800` + `ADC` + `LSR #12`/`LSL #20`), divisor por hardware em
> `0x04000280`/`0x04000290`/`0x04000298`/`0x040002A0`, o loop autônomo da Via A em 5 passos com a matriz
> de diagnóstico de divergências do mwccarm (troca de registos, LSL/ASR extras, acesso a struct, switch,
> pilha, CMP invertido) e o loop da Via B em 4 passos (rastreio no REA, auditoria ARM vs THUMB, patch com
> NCPatcher/armips, verificação pós-patch) — foi integralmente incorporado à skill `nds-rea-modder`.

## Conclusões e Recomendações Arquiteturais

A síntese das evidências recolhidas nos projetos de ponta do ecossistema Nintendo DS demonstra que o sucesso de uma descompilação correspondente em escala industrial — como a reconstrução de 99,73% dos 4,72 MB de código C de *Kingdom Hearts 358/2 Days* (`Yokimitsuro/khdays-decomp`) — não decorre de força bruta manual, mas de uma arquitetura que desacopla o problema em unidades atômicas verificáveis em milissegundos.

Quando essa estrutura é combinada com o servidor MCP do **REA (`morluto/rea`)** e o **Claude Code** numa topologia híbrida **Windows 11 + WSL2 Ubuntu 24.04**, consolidam-se três diretrizes operacionais definitivas para qualquer projeto futuro na plataforma. Primeiramente, o isolamento de escopo para o agente através dos objetos ELF relocalizáveis (`.o`) gerados por módulo pelo `dsd delink` em `build/delinks/` (em vez da abertura monolítica da imagem `.nds`) resolve simultaneamente a colisão de endereços virtuais dos *overlays* na RAM (`0x02140000+`) e o gargalo de inicialização do Ghidra Headless, permitindo que o agente consulte `decompile` e `instructions` com latência mínima e economia de ~145.000 tokens por sessão graças ao perfil `compact`. Em segundo lugar, na preparação de snapshots globais no Ghidra, a desativação do Auto-Analyze padrão em favor do script `SyncDsd.java` do `AetiasHax/dsd-ghidra` preserva a distinção exata entre funções ARM (32-bit), funções Thumb (16-bit) e *literal pools* calculada pelo `dsd`, enquanto a execução do `objdiff-cli diff` em modo texto fecha o loop de *feedback* diretamente no terminal do agente. Por fim, a bifurcação entre a Via A e a Via B deve alinhar-se estritamente ao objetivo final do projeto: para ports nativos de PC ou PS Vita (como `khdays-port` e `KH-DAYS-VITA`), a Via A com `dsd` e o compilador histórico `mwccarm.exe` invocado via interoperabilidade WSL2 é insubstituível; já para modificações de jogabilidade e *ROM hacks*, a Via B utilizando `NCPatcher` e `arm-none-eabi-gcc` (C++20) entrega resultados imediatos, desde que o agente audite sistematicamente o *footprint* de 4 a 8 bytes em hooks sobre código Thumb através da ferramenta `instructions` do REA.
