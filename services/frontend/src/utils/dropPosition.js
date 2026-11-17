// Posición a enviar a POST /cards/:id/move al soltar una tarjeta.
//
// `ids` es el orden completo de la lista destino (incluye tarjetas ocultas por
// el filtro) con la tarjeta ya ubicada donde se previsualizó el drop.
// `isVisible(id)` dice si una tarjeta se ve con el filtro actual.
//
// Con tarjetas ocultas, la posición se ancla a los vecinos VISIBLES para que
// quien mira sin filtro vea un orden coherente:
// - con una visible antes → justo después de ella en el orden completo;
// - al tope (sin visibles antes) → justo antes de la primera visible;
// - sin vecinos visibles → se respeta el lugar de la previsualización.
//
// El backend interpreta `position` como índice en la lista sin la tarjeta
// (ver `_reindex` en board_service.py), por eso se calcula sobre `rest`.
// Sin filtros devuelve lo mismo que `ids.indexOf(cardId)`.
export function dropPosition(ids, cardId, isVisible) {
  const rest = ids.filter((id) => id !== cardId);
  const visible = ids.filter((id) => id === cardId || isVisible(id));
  const i = visible.indexOf(cardId);
  if (i > 0) return rest.indexOf(visible[i - 1]) + 1;
  if (i + 1 < visible.length) return rest.indexOf(visible[i + 1]);
  return Math.max(0, ids.indexOf(cardId));
}

// Orden completo resultante de insertar `cardId` en `position`.
export function placeAt(ids, cardId, position) {
  const rest = ids.filter((id) => id !== cardId);
  rest.splice(position, 0, cardId);
  return rest;
}
