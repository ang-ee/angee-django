import type { ReactElement } from "react";

import EmailPreview from "./EmailPreview";
import { SAMPLE_EMAIL, SAMPLE_TEXT_EMAIL } from "./sample-email";

function EmailFileStory({ raw, name }: { raw: string; name: string }): ReactElement {
  const file = { url: `data:message/rfc822;charset=utf-8,${encodeURIComponent(raw)}`, name, mime: "message/rfc822" };
  return <div className="h-144"><EmailPreview file={file} mime="message/rfc822" /></div>;
}

export default { title: "Messaging/EmailPreview", component: EmailPreview, parameters: { layout: "fullscreen" } };

/** HTML and plain-text alternatives in Spanish, with an inline image; the email's script stays inert in the sandbox. */
export const HtmlAndText = { render: () => <EmailFileStory raw={SAMPLE_EMAIL} name="notice.eml" /> };

export const PlainTextOnly = { render: () => <EmailFileStory raw={SAMPLE_TEXT_EMAIL} name="seating.eml" /> };
