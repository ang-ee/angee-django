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
  IAM_USERS_QUERY,
  type IAMGrantRoleData,
  type IAMGrantRoleVariables,
  type IAMOverviewData,
  type IAMOverviewVariables,
  type IAMUsersData,
  type IAMUsersVariables,
} from "../documents";
import { userLabel } from "../identity-labels";
import {
  roleRef,
  roleRows,
} from "../identity-rows";

const OVERVIEW_COUNT_LIMIT = 1;
const USER_PICKER_LIMIT = 500;

export function OverviewPage(): ReactElement {
  const variables = useMemo<IAMOverviewVariables>(
    () => ({ pagination: { offset: 0, limit: OVERVIEW_COUNT_LIMIT } }),
    [],
  );
  const query = useAuthoredQuery<IAMOverviewData, IAMOverviewVariables>(
    IAM_OVERVIEW_QUERY,
    variables,
  );
  const userVariables = useMemo<IAMUsersVariables>(
    () => ({ pagination: { offset: 0, limit: USER_PICKER_LIMIT } }),
    [],
  );
  const usersQuery = useAuthoredQuery<IAMUsersData, IAMUsersVariables>(
    IAM_USERS_QUERY,
    userVariables,
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
  const users = useMemo(
    () => [...(usersQuery.data?.users.results ?? [])],
    [usersQuery.data],
  );
  const userTotalCount = usersQuery.data?.users.totalCount ?? 0;
  const hasTruncatedUsers = userTotalCount > USER_PICKER_LIMIT;
  const principalOptions = useMemo(
    () =>
      users.map((user) => ({
        value: user.id,
        label: userLabel(user),
      })),
    [users],
  );
  const [selectedPrincipalId, setSelectedPrincipalId] = useState("");
  const [selectedRole, setSelectedRole] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const [grantedRole, setGrantedRole] = useState<{
    principalLabel: string;
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

  useEffect(() => {
    if (
      selectedPrincipalId &&
      !principalOptions.some((option) => option.value === selectedPrincipalId)
    ) {
      setSelectedPrincipalId("");
    }
  }, [principalOptions, selectedPrincipalId]);

  async function handleGrant(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!selectedPrincipalId || !selectedRole) {
      setGrantedRole(null);
      setActionError("Choose a principal and role before granting access.");
      return;
    }

    setActionError(null);
    setGrantedRole(null);
    try {
      const result = await grantRole({
        principalId: selectedPrincipalId,
        role: selectedRole,
      });
      if (result?.grantRole === false) {
        throw new Error("Could not grant role.");
      }
      const selectedPrincipal = users.find(
        (user) => user.id === selectedPrincipalId,
      );
      setSelectedPrincipalId("");
      setGrantedRole({
        principalLabel: selectedPrincipal
          ? userLabel(selectedPrincipal)
          : selectedPrincipalId,
        role: selectedRole,
      });
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
      {usersQuery.error ? (
        <Alert intent="danger" title="Users unavailable">
          {usersQuery.error.message}
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
              <Select
                value={selectedPrincipalId}
                options={principalOptions}
                placeholder={
                  usersQuery.fetching ? "Loading users" : "Select user"
                }
                aria-label="Principal"
                disabled={usersQuery.fetching || principalOptions.length === 0}
                onValueChange={(value) => setSelectedPrincipalId(value)}
              />
              {hasTruncatedUsers ? (
                <span className="text-12 font-normal text-fg-muted">
                  Showing first {USER_PICKER_LIMIT.toLocaleString()} of{" "}
                  {userTotalCount.toLocaleString()} users.
                </span>
              ) : null}
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
                disabled={!selectedPrincipalId || !selectedRole}
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
                <Code>{grantedRole.principalLabel}</Code>
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
