import React, { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { authApi } from '../api';

const AuthContext = createContext(null);

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used within an AuthProvider');
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  const logout = useCallback(() => {
    localStorage.removeItem('token');
    setUser(null);
  }, []);

  useEffect(() => {
    if (!localStorage.getItem('token')) {
      setLoading(false);
      return;
    }
    authApi.me()
      .then(setUser)
      .catch((error) => {
        // Sin respuesta = backend caído: se conserva el token para reintentar.
        if (error.response) logout();
      })
      .finally(() => setLoading(false));
  }, [logout]);

  const startSession = useCallback(({ token, user: loggedUser }) => {
    localStorage.setItem('token', token);
    setUser(loggedUser);
    return loggedUser;
  }, []);

  // Si falta el MFA devuelve `{ mfa: { step, token } }` sin iniciar sesión:
  // la pantalla de login sigue con verifyMfa.
  const login = useCallback(async (username, password) => {
    const result = await authApi.login(username, password);
    if (result.mfa) return result;
    return { user: startSession(result) };
  }, [startSession]);

  const verifyMfa = useCallback(
    async (mfaToken, code) => startSession(await authApi.mfaVerify(mfaToken, code)),
    [startSession],
  );

  // Marca la guía de bienvenida como vista (en el servidor, para que no
  // reaparezca en otro navegador). Si falla, se vuelve a mostrar en el próximo ingreso.
  const completeOnboarding = useCallback(async () => {
    if (user?.onboardingCompleted) return;
    setUser((u) => (u ? { ...u, onboardingCompleted: true } : u));
    try {
      setUser(await authApi.completeOnboarding());
    } catch {
      // sin acción: no es crítico
    }
  }, [user]);

  const updateProfile = useCallback(async (changes) => {
    const updated = await authApi.updateProfile(changes);
    setUser(updated);
    return updated;
  }, []);

  // token = null lo quita. El backend lo valida contra GitHub antes de guardarlo.
  const setGithubToken = useCallback(async (token) => {
    const updated = token ? await authApi.setGithubToken(token) : await authApi.deleteGithubToken();
    setUser(updated);
    return updated;
  }, []);

  // Vuelve a leer el usuario (p. ej. la última corrida de la rutina, que se
  // registra por el MCP y no por esta pestaña). Si falla se conserva el que había.
  const refreshUser = useCallback(async () => {
    try {
      setUser(await authApi.me());
    } catch {
      // sin acción: no es crítico
    }
  }, []);

  // Cambia el tema al instante; si el servidor no lo guarda, vuelve al anterior.
  const setDarkMode = useCallback(async (darkMode) => {
    setUser((u) => (u ? { ...u, darkMode } : u));
    try {
      setUser(await authApi.updateProfile({ darkMode }));
    } catch (error) {
      setUser((u) => (u ? { ...u, darkMode: !darkMode } : u));
      throw error;
    }
  }, []);

  return (
    <AuthContext.Provider value={{
      user, loading, login, verifyMfa, logout, completeOnboarding, updateProfile, setGithubToken, setDarkMode, refreshUser,
      isAdmin: user?.role === 'admin',
    }}>
      {children}
    </AuthContext.Provider>
  );
};
