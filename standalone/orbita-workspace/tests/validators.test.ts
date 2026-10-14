import { test } from 'node:test';
import assert from 'node:assert/strict';
import { accountInput, accountUpdate, homeUrl, profileInput } from '../electron/validators';

test('HTTPS padrão e protocolos explícitos', () => {
  assert.equal(homeUrl('  exemplo.com  '), 'https://exemplo.com/');
  assert.equal(homeUrl('www.exemplo.com/cadastro'), 'https://www.exemplo.com/cadastro');
  assert.equal(homeUrl('https://exemplo.com/'), 'https://exemplo.com/');
  assert.equal(homeUrl('http://localhost:8123/'), 'http://localhost:8123/');
  assert.equal(homeUrl(''), 'about:blank');
});
test('proíbe esquemas perigosos, credenciais e espaços', () => {
  for (const value of ['javascript:alert(1)', 'file:///etc/passwd', 'ftp://example.com',
    'https://user:secret@example.com', 'https://', 'sem espaco.test/a b',
    'data:text/html,<script>alert(1)</script>']) {
    assert.throws(() => homeUrl(value), value);
  }
});
test('valida tipos e estado recebidos do renderer', () => {
  assert.equal(accountInput({ provider:' Site ', displayName:' Principal ' }).provider, 'Site');
  assert.equal(accountInput({provider:'A',displayName:'B'}).status,'draft');
  assert.throws(() => accountInput({provider:'',displayName:'B'}));
  assert.throws(() => accountInput({provider:'A',displayName:'B',status:'active'}));
  assert.throws(() => accountUpdate({id:'not-uuid'}));
  assert.throws(() => profileInput({label:'',homeUrl:'site.test'}));
});
