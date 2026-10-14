'use strict';
// Exemplo de adaptação ao backend HTTP do SEU PROJETO. Não sobe servidor,
// não cria rota pública e não substitui autenticação do backend existente.
// Monte esses handlers em endpoints LOCAIS JÁ PROTEGIDOS POR SESSÃO/CSRF.
function makeLocalHandlers({registration,store,launcher}) {
  return {
    async prepare(body){return registration.prepare(body);},
    async submit(body){return registration.submit(body);},
    async refresh(body){return registration.refresh(body);},
    async list(query){return store.list(query);},
    async open(body){return launcher.open(body.accountId);},
  };
}
module.exports={makeLocalHandlers};
