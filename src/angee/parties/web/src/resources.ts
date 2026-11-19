/** A connected contacts directory: one integration reading an account's address books into parties. */
export const DIRECTORY_MODEL = "parties.Directory";

/** The directories page container each directory vendor addon contributes its connect verb to. */
export const DIRECTORIES_TOOLBAR = "parties.directories#toolbar";

/**
 * Who may connect a directory: the `create` permission `Directory.objects.connect`
 * checks. The administrator role decides it without a row, so a connect entry
 * declares it as `requires` and is absent for anyone the mutation would refuse.
 */
export const DIRECTORY_CREATE = "parties.Directory#create";
