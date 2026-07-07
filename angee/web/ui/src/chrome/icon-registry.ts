import { type ComponentType, type SVGProps } from "react";
import { useAppRuntime } from "../runtime";
import {
  Activity,
  Archive,
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ArrowUpRight,
  Bell,
  BellOff,
  Bold,
  Calendar,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleCheck,
  CircleHelp,
  CircleX,
  CodeXml,
  Columns3,
  Copy,
  Eye,
  FileText,
  Files,
  Filter,
  Folder,
  Grid2X2,
  Grid3X3,
  GripVertical,
  Home,
  Info,
  Italic,
  LayoutDashboard,
  LayoutGrid,
  Link2,
  List,
  ListOrdered,
  LogOut,
  MessageCircle,
  Minus,
  Moon,
  MoreHorizontal,
  MoreVertical,
  PanelLeft,
  PanelRight,
  Paperclip,
  Pencil,
  Plus,
  Quote,
  Search,
  Settings,
  Shield,
  Share2,
  SlidersHorizontal,
  Star,
  Sun,
  Trash2,
  TriangleAlert,
  User,
  Users,
  X,
  GitBranch,
} from "lucide-react";

import { AgentGlyph } from "./AgentGlyph";
import { AngeeMark } from "./AngeeMark";

export type IconProps = SVGProps<SVGSVGElement> & {
  size?: number | string;
  strokeWidth?: number | string;
};

export type IconComponent = ComponentType<IconProps>;

export const baseIcons = {
  activity: Activity,
  agent: AgentGlyph,
  angee: AngeeMark,
  "angee-cube": AngeeMark,
  archive: Archive,
  "arrow-down": ArrowDown,
  "arrow-up": ArrowUp,
  "arrow-up-down": ArrowUpDown,
  "arrow-up-right": ArrowUpRight,
  auth: Shield,
  bell: Bell,
  "bell-off": BellOff,
  bold: Bold,
  calendar: Calendar,
  check: Check,
  "chevron-down": ChevronDown,
  "chevron-left": ChevronLeft,
  "chevron-right": ChevronRight,
  "chevron-up": ChevronUp,
  "circle-check": CircleCheck,
  "circle-x": CircleX,
  "code-xml": CodeXml,
  comments: MessageCircle,
  columns: Columns3,
  copy: Copy,
  eye: Eye,
  file: FileText,
  files: Files,
  filter: Filter,
  folder: Folder,
  grid: Grid3X3,
  "grid-2x2": Grid2X2,
  "grid-3x3": Grid3X3,
  "grip-vertical": GripVertical,
  help: CircleHelp,
  home: Home,
  info: Info,
  italic: Italic,
  "layout-dashboard": LayoutDashboard,
  "layout-grid": LayoutGrid,
  link: Link2,
  list: List,
  "list-ordered": ListOrdered,
  "log-out": LogOut,
  minus: Minus,
  moon: Moon,
  "more-horizontal": MoreHorizontal,
  "more-vertical": MoreVertical,
  notes: FileText,
  "panel-left": PanelLeft,
  "panel-right": PanelRight,
  attachment: Paperclip,
  pencil: Pencil,
  plus: Plus,
  quote: Quote,
  reports: FileText,
  search: Search,
  settings: Settings,
  share: Share2,
  "sliders-horizontal": SlidersHorizontal,
  star: Star,
  sun: Sun,
  trash: Trash2,
  "triangle-alert": TriangleAlert,
  user: User,
  users: Users,
  versions: GitBranch,
  x: X,
} satisfies Readonly<Record<string, IconComponent>>;

export function useIcon(name: string): IconComponent | null {
  const { icons } = useAppRuntime();
  // Fall back to the static base set when the runtime registry lacks the name, so
  // a base glyph (check, chevrons, x, …) still resolves when no AppRuntime is
  // mounted (unit tests, storybook, provider-less embeds). The runtime normally
  // already includes baseIcons (createApp seeds them), so this only matters
  // provider-less; addon-contributed glyphs still require the runtime.
  return getIcon(icons, name) ?? getIcon(baseIcons, name);
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
