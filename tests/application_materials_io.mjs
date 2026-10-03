import assert from 'node:assert/strict';
import {materialsFilename, materialsText} from '../dashboard_assets/application_materials_io.js';

const previous={cover_letter:'Carta anterior',cold_email_subject:'Contato sobre a vaga',cold_email_body:'Corpo anterior'};
const selected={cover_letter:'Carta selecionada',cold_email_subject:'Assunto selecionado',cold_email_body:'Corpo selecionado'};
assert.equal(materialsText(selected,'cover'),'Carta selecionada');
assert.equal(materialsText(selected,'subject'),'Assunto selecionado');
assert.equal(materialsText(selected,'body'),'Corpo selecionado');
assert.equal(materialsText(selected,'email'),'Subject: Assunto selecionado\n\nCorpo selecionado');
assert.notEqual(materialsText(selected,'cover'),materialsText(previous,'cover'));
assert.equal(materialsFilename('../../Coin:base','Manager / CX','cover'),'coin-base_manager-cx_cover-letter.txt');
assert.equal(materialsFilename('Coinbase','Manager, WFM CX Operations','email'),'coinbase_manager-wfm-cx-operations_cold-email.txt');
console.log('Application materials export tests passed.');
