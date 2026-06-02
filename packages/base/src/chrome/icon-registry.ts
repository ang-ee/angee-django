import { type ComponentType, type SVGProps } from "react";
import { useAppRuntime } from "@angee/sdk";
import {
  Activity,
  Archive,
  Bell,
  CircleHelp,
  FileText,
  Home,
  LayoutDashboard,
  List,
  LogOut,
  MessageCircle,
  Search,
  Shield,
  Star,
  Zap,
} from "lucide-react";

import { AngeeMark } from "./AngeeMark";

export type IconProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  strokeWidth?: number | string;
};

export type IconComponent = ComponentType<IconProps>;

export const baseIcons = {
  activity: Activity,
  agent: Zap,
  angee: AngeeMark,
  "angee-cube": AngeeMark,
  archive: Archive,
  auth: Shield,
  bell: Bell,
  comments: MessageCircle,
  file: FileText,
  help: CircleHelp,
  home: Home,
  "layout-dashboard": LayoutDashboard,
  list: List,
  "log-out": LogOut,
  notes: FileText,
  search: Search,
  star: Star,
} satisfies Readonly<Record<string, IconComponent>>;

export function useIcon(name: string): IconComponent | null {
  const { icons } = useAppRuntime();
  return getIcon(icons, name);
}

export function getIcon(
  icons: Readonly<Record<string, unknown>>,
  name: string,
): IconComponent | null {
  const icon = icons[normalizeIconName(name)];
  return isIconComponent(icon) ? icon : null;
}

function isIconComponent(value: unknown): value is IconComponent {
  if (typeof value === "function") return true;
  if (!value || typeof value !== "object") return false;
  return typeof (value as { render?: unknown }).render === "function";
}

function normalizeIconName(name: string): string {
  return name.trim().toLowerCase();
}
