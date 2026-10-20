/** The subject prefix every account holder carries in access rosters and assignments. */
export const USER_SUBJECT_PREFIX = "auth/user:";

/** The access subject of one account (`auth/user:<id>`). */
export function userSubject(userId: string): string {
  return `${USER_SUBJECT_PREFIX}${userId}`;
}

/** The account id behind a user subject, or `null` for any other subject kind. */
export function subjectUserId(subject: string): string | null {
  return subject.startsWith(USER_SUBJECT_PREFIX) ? subject.slice(USER_SUBJECT_PREFIX.length) : null;
}
