// Label que pone "Aprobar" (QA) en los PRs de GitHub: listos para "Mergear PR".
export const APPROVED_LABEL = 'Aprobado';

export const hasApprovedLabel = (pull) => (pull.labels || [])
  .some((l) => l.name.trim().toLowerCase() === APPROVED_LABEL.toLowerCase());

// Label que pone la skill revisar-pr cuando deja comentarios de revisión sin resolver.
export const REVIEWED_LABEL = 'Revisado';

export const hasReviewedLabel = (pull) => (pull.labels || [])
  .some((l) => l.name.trim().toLowerCase() === REVIEWED_LABEL.toLowerCase());

const openPulls = (card) => (card.pulls || []).filter((p) => p.state === 'open');

// Algún PR abierto ya está aprobado (se puede mergear).
export const hasApprovedPull = (card) => openPulls(card).some(hasApprovedLabel);

// Todos los PRs abiertos están aprobados (no queda nada por aprobar).
export const isFullyApproved = (card) => {
  const open = openPulls(card);
  return open.length > 0 && open.every(hasApprovedLabel);
};

// Todos los PRs de la tarjeta ya están mergeados (p. ej. mergeados directo en GitHub):
// solo falta pasarla a Hecho.
export const isFullyMerged = (card) => (card.pulls || []).length > 0
  && card.pulls.every((p) => p.state === 'merged');
