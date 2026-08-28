import { PageObject } from "@angee/e2e";

/** The `/login` page: the username/password form from the rendered binding. */
export class LoginPage extends PageObject {
  readonly path = "/login";

  async signIn(username: string, password: string): Promise<void> {
    await this.page.getByLabel("Username").fill(username);
    await this.page.getByLabel("Password").fill(password);
    await this.page.getByRole("button", { name: "Sign in" }).click();
  }
}
