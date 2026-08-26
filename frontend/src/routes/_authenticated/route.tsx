import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { fetchCurrentUser, type AuthUser } from "@/lib/auth";

export const Route = createFileRoute("/_authenticated")({
  ssr: false,
  // The gate is one question to the backend. It answers with the user or with a
  // 401, and fetchCurrentUser turns the 401 into null after trying to renew the
  // session — so an operator whose access cookie expired mid-shift stays in.
  beforeLoad: async (): Promise<{ user: AuthUser }> => {
    const user = await fetchCurrentUser();
    if (!user) throw redirect({ to: "/auth" });
    return { user };
  },
  component: () => <Outlet />,
});
