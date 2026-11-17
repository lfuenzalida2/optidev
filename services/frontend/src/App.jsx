import React from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { Spin } from 'antd';

import { useAuth } from './contexts/AuthContext';
import Layout from './components/Layout';
import Login from './pages/Login';
import BoardPage from './pages/BoardPage';
import Settings from './pages/Settings';
import Users from './pages/Users';
import Vms from './pages/Vms';
import Account from './pages/Account';
import GithubDashboard from './pages/GithubDashboard';
import Tools from './pages/Tools';
import TestingQueue from './pages/TestingQueue';
import Workflow from './pages/Workflow';
import PreviewDbs from './pages/PreviewDbs';

const RequireAuth = ({ children, admin = false }) => {
  const { user, loading, isAdmin } = useAuth();
  if (loading) return <Spin fullscreen />;
  if (!user) return <Navigate to="/login" replace />;
  if (admin && !isAdmin) return <Navigate to="/" replace />;
  return children;
};

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route element={<RequireAuth><Layout /></RequireAuth>}>
        <Route path="/" element={<BoardPage />} />
        <Route path="/cards/:cardId" element={<BoardPage />} />
        <Route path="/github" element={<GithubDashboard />} />
        <Route path="/testing" element={<TestingQueue />} />
        <Route path="/workflow" element={<Workflow />} />
        <Route path="/bases" element={<PreviewDbs />} />
        <Route path="/settings" element={<RequireAuth admin><Settings /></RequireAuth>} />
        <Route path="/tools" element={<Tools />} />
        <Route path="/users" element={<RequireAuth admin><Users /></RequireAuth>} />
        <Route path="/vms" element={<RequireAuth admin><Vms /></RequireAuth>} />
        <Route path="/account" element={<Account />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
