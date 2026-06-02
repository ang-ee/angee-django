import { type ComponentType, type SVGProps } from "react";
import { useAppRuntime } from "@angee/sdk";
import {
  Activity,
  Archive,
  Bell,
  BellOff,
  Calendar,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleCheck,
  CircleHelp,
  CircleX,
  FileText,
  Files,
  Grid3X3,
  Home,
  Info,
  LayoutDashboard,
  List,
  LogOut,
  MessageCircle,
  Minus,
  PanelRight,
  Plus,
  Search,
  Settings,
  Shield,
  Star,
  TriangleAlert,
  User,
  Users,
  X,
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
  "bell-off": BellOff,
  calendar: Calendar,
  check: Check,
  "chevron-down": ChevronDown,
  "chevron-left": ChevronLeft,
  "chevron-right": ChevronRight,
  "chevron-up": ChevronUp,
  "circle-check": CircleCheck,
  "circle-x": CircleX,
  comments: MessageCircle,
  file: FileText,
  files: Files,
  grid: Grid3X3,
  "grid-3x3": Grid3X3,
  help: CircleHelp,
  home: Home,
  info: Info,
  "layout-dashboard": LayoutDashboard,
  list: List,
  "log-out": LogOut,
  minus: Minus,
  notes: FileText,
  "panel-right": PanelRight,
  plus: Plus,
  reports: FileText,
  search: Search,
  settings: Settings,
  star: Star,
  "triangle-alert": TriangleAlert,
  user: User,
  users: Users,
  x: X,
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
