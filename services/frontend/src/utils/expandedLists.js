// Listas "ocultables" (collapsible) que el usuario expandió en el tablero.
// Por defecto esas listas se muestran colapsadas; acá se guarda la excepción.
// Es una preferencia visual por usuario y navegador: si localStorage no está
// disponible (modo privado, bloqueado) se cae al valor por defecto.

const storageKey = (userId) => `optidev:expandedLists:${userId ?? 'anon'}`;

export function readExpandedLists(userId) {
  try {
    const ids = JSON.parse(localStorage.getItem(storageKey(userId)) || '[]');
    return new Set(Array.isArray(ids) ? ids.filter(Number.isInteger) : []);
  } catch {
    return new Set();
  }
}

export function writeExpandedLists(userId, ids) {
  try {
    localStorage.setItem(storageKey(userId), JSON.stringify([...ids]));
  } catch {
    // sin acción: la preferencia solo dura mientras la página esté abierta
  }
}
