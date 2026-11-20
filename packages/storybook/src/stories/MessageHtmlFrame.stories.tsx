import type { Meta, StoryObj } from "@storybook/react-vite";
import { MessageHtmlFrame } from "@angee/ui";

// A one-pixel transparent PNG stands in for an inline (cid:) image.
const PIXEL = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=";

const BODY = `
  <h2 style="margin:0 0 8px">Reading circle this Thursday</h2>
  <p>Doors open at <b>eight</b>. Bring the chapter you marked.</p>
  <p><img alt="Banner" src="https://images.example.test/banner.png" width="320" height="80"></p>
  <p><img alt="Mark" src="cid:mark@example.test" width="24" height="24"> North studio</p>
  <p><a href="https://example.test/circle">Event page</a></p>
  <script>document.body.textContent = "scripted"</script>
`;

const meta = {
  title: "Communication/MessageHtmlFrame",
  component: MessageHtmlFrame,
  parameters: { layout: "padded" },
  args: { title: "HTML message body", html: BODY, inlineUrls: { "mark@example.test": PIXEL } },
} satisfies Meta<typeof MessageHtmlFrame>;

export default meta;

type Story = StoryObj<typeof meta>;

/** Remote images wait behind the frame's control; the inline image loads and the script stays inert. */
export const Default: Story = {
  render: (args) => <div className="max-w-2xl"><MessageHtmlFrame {...args} /></div>,
};

export const RemoteImagesLoaded: Story = {
  args: { remoteImages: true },
  render: (args) => <div className="max-w-2xl"><MessageHtmlFrame {...args} /></div>,
};

export const SpanishBody: Story = {
  args: { lang: "es", html: "<p>Las puertas abren a las <b>ocho</b>.</p>" },
  render: (args) => <div className="max-w-2xl"><MessageHtmlFrame {...args} /></div>,
};
