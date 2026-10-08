import { LogoLoader } from "../ui/LogoLoader";
import { lazy, Suspense } from "react";
import { useTranslation } from "react-i18next";
import { createBrowserRouter, Navigate, RouterProvider, Route, Routes } from "react-router-dom";
import { AuthPage } from "../features/auth/AuthPage";
import { AuthProvider, useAuth } from "../features/auth/AuthProvider";
import { useRequireAuth } from "../features/auth/useRequireAuth";
import { Text } from "../ui/mui";

const DesignPage = lazy(() => import("../pages/DesignPage").then(m => ({ default: m.DesignPage })));
const AdminAuditPage = lazy(() => import("../pages/AdminAuditPage").then(m => ({ default: m.AdminAuditPage })));

const router=createBrowserRouter([
  // The local image library is gone: editing a local image opens from the design page.
  {path:"/editor/*",element:<Navigate to="/v2" replace/>},
  {path:"*",element:<Suspense fallback={<Text role="status">正在打开…</Text>}><AuthProvider><AppRoutes/></AuthProvider></Suspense>},
]);
export function App() {
  return <RouterProvider router={router}/>;
}
function AppRoutes() {
  const { t } = useTranslation();
  const { isLoading } = useAuth();
  const auth = useRequireAuth();
  if (isLoading) return <div role="status" aria-label={t("auth.checkingSession")} className="kc-boot-loader"><LogoLoader size={96} /></div>;
  return <>
    <Suspense fallback={<Text role="status">正在打开…</Text>}>
      <Routes>
        <Route element={<AuthPage />} path="/login" />
        <Route element={<AdminAuditPage />} path="/admin/audit" />
        <Route element={<AdminAuditPage />} path="/admin" />
        <Route element={<DesignPage auth={auth} />} path="/v2/:designId?" />
        {/* V1 is retired: every other address opens V2. */}
        <Route element={<Navigate to="/v2" replace />} path="*" />
      </Routes>
    </Suspense>
    {auth.authDialog}
  </>;
}
