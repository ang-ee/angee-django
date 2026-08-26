import * as React from "react";
import type { Row } from "@angee/metadata";
import { useClientResourceViewSurface, useGroupedResourceViewSurface, useResourceViewSurface, type GroupedResourceViewSurface, type ResourceViewSurface, type UseResourceViewSurfaceProps } from "../resource-view-surface";
interface SurfaceBodyProps<TRow extends Row> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  children: (surface: ResourceViewSurface<TRow>) => React.ReactElement;
}

interface GroupedSurfaceBodyProps<TRow extends Row> {
  surfaceProps: UseResourceViewSurfaceProps<TRow>;
  children: (surface: GroupedResourceViewSurface<TRow>) => React.ReactElement;
}

export function ServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useResourceViewSurface(surfaceProps));
}

export function GroupedServerSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: GroupedSurfaceBodyProps<TRow>): React.ReactElement {
  return children(useGroupedResourceViewSurface(surfaceProps));
}

export function ClientSurfaceBody<TRow extends Row>({
  surfaceProps,
  children,
}: SurfaceBodyProps<TRow>): React.ReactElement {
  return children(useClientResourceViewSurface(surfaceProps));
}
