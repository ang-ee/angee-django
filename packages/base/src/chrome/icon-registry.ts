import { createElement, type ComponentType, type SVGProps } from "react";
import { AngeeLogo } from "@angee/logo-react";
import {
  Activity,
  Archive,
  Bell,
  BookOpen,
  Calendar,
  CircleHelp,
  Database,
  FileText,
  Folder,
  HelpCircle,
  Home,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageCircle,
  Search,
  Settings,
  Shield,
  Star,
  User,
  Users,
  Workflow,
  Zap,
} from "lucide-react";

export type IconProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  strokeWidth?: number | string;
};

export type IconComponent = ComponentType<IconProps>;

function AngeeCubeIcon({ size = 20, strokeWidth, ...props }: IconProps) {
  const pixelSize = typeof size === "number" ? size : undefined;
  return createElement(AngeeLogo, {
    ...props,
    bgColor: null,
    geometry: "cube",
    preset: "gold",
    size: pixelSize,
    strokeWidth: typeof strokeWidth === "number" ? strokeWidth : undefined,
    width: size,
    height: size,
  });
}

const icons = new Map<string, IconComponent>([
  ["activity", Activity],
  ["agent", Zap],
  ["angee", AngeeCubeIcon],
  ["angee-cube", AngeeCubeIcon],
  ["archive", Archive],
  ["auth", Shield],
  ["bell", Bell],
  ["book", BookOpen],
  ["calendar", Calendar],
  ["comments", MessageCircle],
  ["dashboard", LayoutDashboard],
  ["data", Database],
  ["database", Database],
  ["file", FileText],
  ["files", Folder],
  ["folder", Folder],
  ["help", CircleHelp],
  ["help-circle", HelpCircle],
  ["home", Home],
  ["layout-dashboard", LayoutDashboard],
  ["log-out", LogOut],
  ["menu", Menu],
  ["messages", MessageCircle],
  ["notes", FileText],
  ["search", Search],
  ["settings", Settings],
  ["star", Star],
  ["user", User],
  ["users", Users],
  ["workflow", Workflow],
  ["workflows", Workflow],
  ["zap", Zap],
]);

export function getIcon(name: string): IconComponent | null {
  return icons.get(normalizeIconName(name)) ?? null;
}

export function registerIcon(name: string, icon: IconComponent): void {
  icons.set(normalizeIconName(name), icon);
}

function normalizeIconName(name: string): string {
  return name.trim().toLowerCase();
}
