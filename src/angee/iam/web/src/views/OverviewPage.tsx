import {
  useEffect,
  useMemo,
  useState,
  type FormEvent,
  type ReactElement,
} from "react";

import {
  Alert,
  Button,
  Code,
  Input,
  MetricGrid,
  Select,
  SurfacePanel,
} from "@angee/base";
import {
  useAuthoredMutation,
  useAuthoredQuery,
} from "@angee/sdk";

import {
  IAM_GRANT_ROLE_MUTATION,
  IAM_OVERVIEW_QUERY,
  type IAMGrantRoleData,
  type IAMGrantRoleVariables,
  type IAMOverviewData,
  type IAMOverviewVariables,
} from "../documents";
import {
  roleRef,
  roleRows,
} from "../identity-rows";

const OVERVIEW_COUNT_LIMIT = 1;

export function OverviewPage(): ReactElement {
  const variables = useMemo<IAMOverviewVariables>(
    () => ({ pagination: { offset: 0, limit: OVERVIEW_COUNT_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<IAMOverviewData, IAMOverviewVariables>(
    IAM_OVERVIEW_QUERY,
    variables,
  );
  const [grantRole, grantState] = useAuthoredMutation<
    IAMGrantRoleData,
    IAMGrantRoleVariables
  >(IAM_GRANT_ROLE_MUTATION);
  const roles = useMemo(
    () => roleRows(query.data?.roles ?? []),
    [query.data],
  );
  const roleOptions = useMemo(
    () =>
      roles.map((role) => ({
        value: roleRef(role),
        label: `${role.namespace} / ${role.label}`,
      })),
    [roles],
  );
  const [principalId, setPrincipalId] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [grantedRole, setGrantedRole] = useState<{
    principalId: string;
    role: string;
  } | null>(null);

  useEffect(() => {
    if (roleOptions.length === 0) {
      setSelectedRole("");
      return;
    }
    if (!roleOptions.some((option) => option.value === selectedRole)) {
      setSelectedRole(roleOptions[0]?.value ?? "");
    }
  }, [roleOptions, selectedRole]);

  async function handleGrant(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    const trimmedPrincipalId = principalId.trim();
    if (!trimmedPrincipalId || !selectedRole) {
      setGrantedRole(null);
      setActionError("Choose a principal and role before granting access.");
      return;
    }

    setActionError(null);
    setGrantedRole(null);
    try {
      const result = await grantRole({
        principalId: trimmedPrincipalId,
        role: selectedRole,
      });
      if (result?.grantRole === false) {
        throw new Error("Could not grant role.");
      }
      setPrincipalId("");
      setGrantedRole({ principalId: trimmedPrincipalId, role: selectedRole });
      query.refetch();
    } catch (caught) {
      setActionError(
        caught instanceof Error ? caught.message : "Could not grant role.",
      );
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {query.error ? (
        <Alert intent="danger" title="Identity overview unavailable">
          {query.error.message}
        </Alert>
      ) : null}
      <MetricGrid
        metrics={[
          {
            label: "Users",
            value: overviewCount(query.data?.users.totalCount, query.fetching),
            icon: "users",
          },
          {
            label: "Roles",
            value: overviewCount(query.data?.roles.length, query.fetching),
            icon: "auth",
            variant: "info",
          },
          {
            label: "Grants",
            value: overviewCount(query.data?.grants.totalCount, query.fetching),
            icon: "check",
            variant: "success",
          },
          {
            label: "Relationships",
            value: overviewCount(
              query.data?.relationships.totalCount,
              query.fetching,
            ),
            icon: "share",
            variant: "warning",
          },
        ]}
      />
      <SurfacePanel title="Grant Role" summary={`${roles.length} roles`}>
        <div className="p-4">
          <form
            className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(14rem,18rem)_auto]"
            onSubmit={(event) => {
              void handleGrant(event);
            }}
          >
            <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
              Principal
              <Input
                value={principalId}
                placeholder="User ID"
                autoComplete="off"
                onChange={(event) => setPrincipalId(event.currentTarget.value)}
              />
            </label>
            <label className="grid min-w-0 gap-1.5 text-13 font-medium text-fg">
              Role
              <Select
                value={selectedRole}
                options={roleOptions}
                placeholder="Select role"
                aria-label="Role"
                onValueChange={(value) => setSelectedRole(value)}
              />
            </label>
            <div className="flex items-end">
              <Button
                type="submit"
                variant="primary"
                pending={grantState.fetching}
                disabled={!principalId.trim() || !selectedRole}
              >
                Grant
              </Button>
            </div>
          </form>
          {actionError ? (
            <Alert className="mt-3" intent="danger" title="Role was not granted">
              {actionError}
            </Alert>
          ) : null}
          {grantedRole ? (
            <Alert className="mt-3" intent="success" title="Role granted">
              <span className="inline-flex min-w-0 flex-wrap items-center gap-1">
                <Code>{grantedRole.role}</Code>
                <span>to</span>
                <Code>{grantedRole.principalId}</Code>
              </span>
            </Alert>
          ) : null}
        </div>
      </SurfacePanel>
    </div>
  );
}

function overviewCount(
  value: number | undefined,
  fetching: boolean,
): ReactElement | string {
  if (value === undefined && fetching) return "Loading";
  return (value ?? 0).toLocaleString();
}
