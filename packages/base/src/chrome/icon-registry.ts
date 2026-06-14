import { type ComponentType, type SVGProps } from "react";
import { useAppRuntime } from "@angee/sdk";
import {
  Activity,
  Archive,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
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
  Columns3,
  Eye,
  FileText,
  Files,
  Filter,
  Folder,
  Grid2X2,
  Grid3X3,
  Home,
  Info,
  LayoutDashboard,
  LayoutGrid,
  List,
  LogOut,
  MessageCircle,
  Minus,
  MoreVertical,
  PanelRight,
  Paperclip,
  Plus,
  Search,
  Settings,
  Shield,
  Share2,
  SlidersHorizontal,
  Star,
  Trash2,
  TriangleAlert,
  User,
  Users,
  X,
  Zap,
  GitBranch,
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
  "arrow-down": ArrowDown,
  "arrow-up": ArrowUp,
  "arrow-up-down": ArrowUpDown,
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
  columns: Columns3,
  eye: Eye,
  file: FileText,
  files: Files,
  filter: Filter,
  folder: Folder,
  grid: Grid3X3,
  "grid-2x2": Grid2X2,
  "grid-3x3": Grid3X3,
  help: CircleHelp,
  home: Home,
  info: Info,
  "layout-dashboard": LayoutDashboard,
  "layout-grid": LayoutGrid,
  list: List,
  "log-out": LogOut,
  minus: Minus,
  "more-vertical": MoreVertical,
  notes: FileText,
  "panel-right": PanelRight,
  attachment: Paperclip,
  plus: Plus,
  reports: FileText,
  search: Search,
  settings: Settings,
  share: Share2,
  "sliders-horizontal": SlidersHorizontal,
  star: Star,
  trash: Trash2,
  "triangle-alert": TriangleAlert,
  user: User,
  users: Users,
  versions: GitBranch,
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
