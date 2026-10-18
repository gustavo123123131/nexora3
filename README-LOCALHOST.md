# Lixo CPA pelo navegador — adaptação mínima

O Nexora utiliza os módulos de navegador de `Settings`, com gerenciamento de contas, perfis isolados, proxies e recursos de registro presentes nesta versão. A pasta `webui` apresenta os controles em um navegador por uma API Python local. O painel pode se comunicar com o agente Windows associado; quando executado exclusivamente no computador local, usa o endereço `127.0.0.1`.

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
- A aba **Chave Pix** gerencia registros locais. A seção **Operações** pode tentar vincular a chave na sessão autenticada da conta selecionada. A seção **Verificar saque** consulta informações e não envia pedidos. Os testes automatizados não devem executar operações externas.
- `tkinter.messagebox` e `tkinter.simpledialog` são transportados para prompts/alertas nativos do navegador. Janelas de seleção personalizadas de `CTkToplevel` são modais HTML.
- A interface do painel usa um único estado global do programa: **apenas um usuário local** deve utilizá-la. O servidor inclui proteção básica contra acesso por host/origem estranhos. Nunca altere o endereço do servidor para `0.0.0.0` sem adicionar autenticação.
- Os diretórios `Settings/Criar_Mae/__profiles` e `Settings/Criar_Filha/__profiles` incluem cookies, sessões e caches do Chromium. Devem continuar locais e privados. Arquivos do projeto original foram mantidos no ZIP final. Não faça upload desse ZIP para locais públicos.

## Diagnóstico

- Acesse `/api/state` para verificar o estado dos controles (somente localhost). O servidor registra erros de callbacks no terminal.
- Se o Playwright disser que não encontra o navegador: `py -m playwright install chromium`.
- Para alterar HTML/CSS, edite `webui/static/`. Para adicionar suporte a widgets, edite `webui/ctk_compat.py`. O código da regra de negócio está nas pastas originais de `Settings/`.

**Limitação de testes**: os testes automatizados locais exercitam inicialização, abas, controles, vinculação de sessões e fluxos simulados, mas não validam operações reais em plataformas externas.


## Saque por API na sessão da conta (5GBet)

- Botão **Sacar — enviar pedido real** na seção Operações: consulta destino remoto único, saldo e taxa; usa o PIN de saque **já salvo no cofre do Windows**; envia uma única tentativa `cashV3` pela sessão Chromium correspondente; consulta `orderInfo` e registra número e estado observado.
- É necessário salvar o PIN em **Contas → Detalhes**, abrir e autenticar o Chromium da conta e confirmar que existe exatamente um destino Pix vinculado. O botão não cria PIN, contas, destinos nem novas sessões.
- Um pedido aceito não é sinônimo de pagamento concluído: a interface mantém o estado de acompanhamento até comprovação independente. **Atualizar estado do pedido** consulta novamente `orderInfo` sem enviar outro saque.
- Tentativas incertas, pendentes ou aceitas bloqueiam novo envio para a mesma conta, inclusive após reinicialização. Em caso de timeout, confira a plataforma antes de qualquer intervenção manual.
- **Limitação:** o script de referência demonstrou aceitação em uma sessão específica da `www.5gbet.com`; isso não comprova o contrato das demais casas. O envio é restrito a essa origem. Testes automatizados usam objetos simulados, não movimentam dinheiro.
