import axios from 'axios';

const axiosApi = axios.create({ baseURL: '/api' });

// FastAPI devuelve el error en `detail`: string, objeto o array de pydantic.
export const extractErrorMessage = (error) => {
  const detail = error?.response?.data?.detail;
  if (typeof detail === 'string' && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => {
      const field = Array.isArray(d?.loc) ? d.loc.filter((p) => p !== 'body').join('.') : '';
      return field ? `${field}: ${d.msg}` : d?.msg || JSON.stringify(d);
    }).join('; ');
  }
  if (error?.message) return error.message;
  return 'Ocurrió un error inesperado';
};

axiosApi.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

axiosApi.interceptors.response.use(
  (response) => response,
  (error) => {
    const url = error.config?.url || '';
    // Un 401 en login, MFA o cambio de contraseña es "credenciales inválidas", no sesión expirada.
    const isAuthFlow = ['/auth/login', '/auth/mfa/', '/auth/change-password'].some((p) => url.includes(p));
    if (error.response?.status === 401 && !isAuthFlow) {
      localStorage.removeItem('token');
      if (window.location.pathname !== '/login') window.location.href = '/login';
    }
    return Promise.reject(error);
  }
);

export default axiosApi;
