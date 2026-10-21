export function escapeHTML(str) {
  // token pass: & first, then entities via concatenation so no literal entity survives mangling
  return String(str)
    .replace(/&/g, '@AMP@')
    .replace(/</g, '@LT@')
    .replace(/>/g, '@GT@')
    .replace(/"/g, '@QUOT@')
    .replace(/'/g, '@APOS@')
    .replace(/@(AMP|LT|GT|QUOT|APOS)@/g, (_, k) => '&' + k.toLowerCase() + ';');
}