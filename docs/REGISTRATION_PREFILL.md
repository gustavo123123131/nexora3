# Pré-preenchimento de cadastro no navegador Nexora

Ao clicar em Abrir Conta, o sistema já gera o perfil e um registro em accounts.json.
O novo módulo nexora_accounts/registration_autofill.py recebe o profile_record_id
existente e roda na mesma página Playwright aberta pelo Multi Login.

Funções: validar domínio da página, localizar o formulário com os seletores
account, userpass, confirmPassword, realName, email e phone, e preencher apenas
campos visíveis com os dados daquele perfil. O CPF só é inserido quando
cpfVerified for true. O envio final fica sob confirmação do titular no navegador.

Proteções: perfis marcados isTestData só podem ser usados em páginas locais
de demonstração, nunca para cadastrar identidade fictícia em plataformas reais.
Para preenchimento real, os dados precisam ter isTestData=false e
ownerConfirmed=true. O módulo não chama APIs HTTP privadas, não aceita termos
automaticamente, não contorna CAPTCHA e não marca cadastro como concluído.

Arquivos: nexora_accounts/registration_autofill.py, testes em
tests/test_registration_autofill.py, conexão em ambos os arquivos
Settings/Criar_Mae/gui_tab.py e Settings/Criar_Filha/gui_tab.py, além da
exibição de status em webui/static/accounts_view.js.

API local existente para visualizar contas: GET /api/registration/accounts.

Teste: python -m pytest tests/test_registration_autofill.py -q

Este módulo implementa apenas o pré-preenchimento na página já aberta.
A criação externa só é confirmada pela plataforma após o envio autorizado.
