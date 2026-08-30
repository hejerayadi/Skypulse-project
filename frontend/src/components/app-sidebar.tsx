import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import { LayoutDashboard, LogOut, Map, Moon, Sun, Satellite } from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarFooter,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  useSidebar,
} from "@/components/ui/sidebar";
import { SENSORS } from "@/lib/sensors";
import { logout } from "@/lib/auth";
import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

export function AppSidebar() {
  const { state } = useSidebar();
  const collapsed = state === "collapsed";
  const pathname = useRouterState({ select: (r) => r.location.pathname });
  const navigate = useNavigate();
  const { theme, toggle } = useTheme();

  const isActive = (path: string) => pathname === path;

  async function signOut() {
    // The backend revokes the session and clears the cookies; there is no
    // client-side session state left to tidy up here.
    await logout();
    toast.success("Signed out");
    navigate({ to: "/auth" });
  }

  return (
    <Sidebar collapsible="icon" data-tour="app-sidebar">
      <SidebarHeader>
        <div className="flex items-center gap-2 px-2 py-2">
          <div
            className="grid h-8 w-8 shrink-0 place-items-center rounded-lg"
            style={{ background: "var(--gradient-primary)" }}
          >
            <Satellite className="h-4 w-4 text-primary-foreground" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <div className="truncate text-sm font-semibold tracking-tight">SkyPulse</div>
              <div className="truncate text-[11px] text-muted-foreground">Ground Station</div>
            </div>
          )}
        </div>
      </SidebarHeader>

      <SidebarContent>
        <SidebarGroup>
          <SidebarGroupLabel>Overview</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              <SidebarMenuItem>
                <SidebarMenuButton asChild isActive={isActive("/dashboard")} tooltip="All sensors">
                  <Link to="/dashboard">
                    <LayoutDashboard className="h-4 w-4" />
                    <span>All sensors</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
              <SidebarMenuItem>
                <SidebarMenuButton
                  asChild
                  isActive={isActive("/dashboard/nodes")}
                  tooltip="Node map"
                >
                  <Link to="/dashboard/nodes">
                    <Map className="h-4 w-4" />
                    <span>Node map</span>
                  </Link>
                </SidebarMenuButton>
              </SidebarMenuItem>
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>

        <SidebarGroup>
          <SidebarGroupLabel>Telemetry</SidebarGroupLabel>
          <SidebarGroupContent>
            <SidebarMenu>
              {SENSORS.map((s) => {
                const Icon = s.icon;
                return (
                  <SidebarMenuItem key={s.id}>
                    <SidebarMenuButton
                      asChild
                      isActive={isActive(`/dashboard/${s.id}`)}
                      tooltip={s.name}
                    >
                      <Link to="/dashboard/$sensor" params={{ sensor: s.id }}>
                        <Icon className="h-4 w-4" />
                        <span>{s.name}</span>
                      </Link>
                    </SidebarMenuButton>
                  </SidebarMenuItem>
                );
              })}
            </SidebarMenu>
          </SidebarGroupContent>
        </SidebarGroup>
      </SidebarContent>

      <SidebarFooter>
        <div className="flex items-center gap-1 px-1">
          <Button variant="ghost" size="sm" className="flex-1 justify-start" onClick={toggle}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {!collapsed && <span className="ml-2">{theme === "dark" ? "Light" : "Dark"}</span>}
          </Button>
          <Button variant="ghost" size="sm" onClick={signOut} aria-label="Sign out">
            <LogOut className="h-4 w-4" />
          </Button>
        </div>
      </SidebarFooter>
    </Sidebar>
  );
}
