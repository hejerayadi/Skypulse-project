import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";
import type { User } from "@supabase/supabase-js";
import { supabase } from "@/integrations/supabase/client";

// Local dev escape hatch: with VITE_DISABLE_AUTH="true" in frontend/.env the
// dashboard opens straight from the URL, no Supabase session required.
const AUTH_DISABLED = import.meta.env.VITE_DISABLE_AUTH === "true";

export const Route = createFileRoute("/_authenticated")({
  ssr: false,
  beforeLoad: async (): Promise<{ user: User | null }> => {
    if (AUTH_DISABLED) return { user: null };
    const { data, error } = await supabase.auth.getUser();
    if (error || !data.user) throw redirect({ to: "/auth" });
    return { user: data.user };
  },
  component: () => <Outlet />,
});
