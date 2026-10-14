# Lixo CPA pelo navegador — adaptação mínima

O `main.py` e todos os módulos originais dentro de `Settings` foram mantidos **sem alterações**. A pasta `webui` emula os componentes visuais mais usados de `customtkinter` e mostra as telas originais num navegador por uma API Python local. O servidor não envia a terceiros dados de contas ou credenciais por design; roda em `127.0.0.1`.

## Windows (ambiente original)

1. Instale Python 3.11+ com a opção **Add python.exe to PATH**. O Python deve incluir a biblioteca `tkinter`, que já vem normalmente na distribuição padrão do Windows.
2. Extraia o arquivo ZIP mantendo a pasta `Settings` junto de `main.py` e `servidor.py`.
3. Execute `iniciar-localhost.bat` na raiz do projeto ou use o terminal:

```powershell
py -m pip install -r requirements-web.txt
py -m playwright install chromium
py servidor.py
```

4. Abra **http://127.0.0.1:8765** no navegador, no mesmo computador. Não exponha esse servidor na internet.

## Linux / macOS

```bash
python3 -m pip install -r requirements-web.txt
python3 -m playwright install chromium
python3 servidor.py
```

Em Linux, `python3-tk` pode ser necessário para carregar o nome de módulo `tkinter`, mesmo sem abrir janelas GUI.

## Desenvolvimento

O padrão é ativar `uvicorn` com reload: a cada alteração de `.py`, `.js`, `.css` ou `.html`, o servidor reinicia. Quando a conexão for restabelecida, o navegador recarrega a página automaticamente. Os módulos de lógica originais permanecem iguais. Para desativar o reload: `python servidor.py --no-reload`. Para outra porta: `python servidor.py --port 9000` e acesse `http://127.0.0.1:9000`.

## Diferenças importantes vs. aplicativo desktop

- A interface apresenta os controles originais, mas **a aparência de widgets do CustomTkinter é aproximada em CSS**. Não há garantia de fidelidade pixel a pixel em todos os tamanhos de tela.
- As instâncias Playwright permanecem **no computador onde o servidor Python roda**, em janelas Chromium separadas ou modo headless, como antes. Elas **não são embutidas** em abas do navegador do painel. Sites de terceiros não permitem incorporação universal e os perfis do Chromium são vinculados ao navegador controlado.
- A aba **Área Pix** pode iniciar sessões de bancos externas ao painel; sua automação exige login/autorizações e acesso às integrações pré-existentes. Nenhuma ação real de cadastro, saque ou transação bancária é feita nos testes.
- `tkinter.messagebox` e `tkinter.simpledialog` são transportados para prompts/alertas nativos do navegador. Janelas de seleção personalizadas de `CTkToplevel` são modais HTML.
- A interface do painel usa um único estado global do programa: **apenas um usuário local** deve utilizá-la. O servidor inclui proteção básica contra acesso por host/origem estranhos. Nunca altere o endereço do servidor para `0.0.0.0` sem adicionar autenticação.
- Os diretórios `Settings/Criar_Mae/__profiles` e `Settings/Criar_Filha/__profiles` incluem cookies, sessões e caches do Chromium. Devem continuar locais e privados. Arquivos do projeto original foram mantidos no ZIP final. Não faça upload desse ZIP para locais públicos.

## Diagnóstico

- Acesse `/api/state` para verificar o estado dos controles (somente localhost). O servidor registra erros de callbacks no terminal.
- Se o Playwright disser que não encontra o navegador: `py -m playwright install chromium`.
- Para alterar HTML/CSS, edite `webui/static/`. Para adicionar suporte a widgets, edite `webui/ctk_compat.py`. O código da regra de negócio está nas pastas originais de `Settings/`.

**Limitação de testes**: os testes automatizados locais exercitam inicialização, abas, controles, sincronização de opções e bloqueio de origens, mas não simulam logins de bancos ou cadastros em serviços externos.
