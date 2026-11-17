// Texto de cada entrada del historial de una tarjeta (card_activity).
const ACTION_LABELS = {
  created: (d) => `creó la tarjeta en "${d.list}"`,
  moved: (d) => `movió la tarjeta de "${d.from}" a "${d.to}"`,
  updated: (d) => `editó ${(d.fields || []).join(', ')}`,
  archived: () => 'archivó la tarjeta',
  unarchived: () => 'restauró la tarjeta',
  commented: () => 'comentó',
  pr_added: (d) => `agregó el PR ${d.url}`,
  merge_attempted: (d) => {
    const results = d.results || [];
    const merged = results.filter((r) => r.status === 'merged').length;
    return `usó "Mergear PR": ${merged} de ${results.length} PR(s) mergeado(s)`;
  },
  rebase_attempted: (d) => {
    const results = d.results || [];
    const rebased = results.filter((r) => r.status === 'rebased').length;
    return `usó "Rebase": ${rebased} de ${results.length} PR(s) al día con su rama base`;
  },
  approved: (d) => `aprobó QA y puso el label "${d.label}" en ${(d.pulls || []).length} PR(s)`,
  rejected: (d) => `rechazó QA y devolvió ${(d.pulls || []).length} PR(s) a "${d.label}"`,
};

export const describeActivity = (a) => (ACTION_LABELS[a.action] || (() => a.action))(a.data || {});
