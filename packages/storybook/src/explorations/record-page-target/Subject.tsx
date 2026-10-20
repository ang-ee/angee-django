import { Fragment, useState, type ReactNode } from "react";
import { useForm } from "react-hook-form";
import {
  AppRuntimeProvider, Avatar, AvatarStack, Button, Chip, DropdownMenu, EditableLines, Field, FieldRow,
  FormGrid, FormView, Glyph, Group, MessageFeed, MessageRow, PaneLabel,
  PaneRow, PaneRows, Popover, PreviewPane, StatusbarSteps, Tabs, Tooltip, cn,
  parsePageGroups, statusTone, textRoleVariants, type PreviewProvider, type WidgetDefinition,
} from "@angee/ui";
import {
  access, agreementMarkdown, bill, conversations, fileDetails, invoiceXml, lineMetadata, message,
  needsAttention, recordHeaders,
  type AsideTab, type PageKind,
} from "./fixtures";
import { IconControl, Spec } from "./spec";

// The same shipped contribution the money addon registers in composed apps.
const moneyOwners = import.meta.glob<{
  moneyWidget: WidgetDefinition<string | number | null>;
  formatMoney: (value: string | number | null | undefined, code: string | undefined) => string;
}>("../../../../../addons/angee/money/web/src/widgets/money.tsx", { eager: true });
const moneyOwner = Object.values(moneyOwners)[0]!;

export function Subject({ page, messageOpen, onMessage, onAside }: {
  page: PageKind; messageOpen: boolean; onMessage: (open: boolean) => void; onAside: (tab: AsideTab) => void;
}) {
  return <main className="target-subject" data-region="subject" aria-label="Subject">
    <CommandBand page={page} messageOpen={messageOpen} onMessage={onMessage} onAside={onAside} />
    <div className="target-subject-scroll">
      <div className="target-sheet">
        <RecordHeader page={page} messageOpen={messageOpen} />
        {page === "bill" ? <div className="target-bill-body"><BillFields /><DocumentPane /></div>
          : page === "file" ? <FileBody />
          : page === "knowledge" ? <div className="pb-10"><PreviewPane file={{
            url: `data:text/markdown;charset=utf-8,${encodeURIComponent(agreementMarkdown)}`, name: bill.agreement, mime: "text/markdown",
          }} /></div>
          : messageOpen ? <MessageReader onBack={() => onMessage(false)} /> : <InboxList onOpen={() => onMessage(true)} />}
      </div>
    </div>
  </main>;
}

function RecordHeader({ page, messageOpen }: { page: PageKind; messageOpen: boolean }) {
  const details = recordHeaders[page === "nexus" && messageOpen ? "nexusMessage" : page];
  return <Spec owner="RecordHeader" className="target-record-header">
    <header>
      <p className={cn(textRoleVariants({ role: "caption" }), "mb-2 uppercase tracking-wide")}>{details.eyebrow}</p>
      <div className="flex items-center justify-between gap-3">
        <h1 className={cn(textRoleVariants({ role: "display" }), "min-w-0 leading-tight")}>{details.title}</h1>
        {page === "bill" ? <StatusbarSteps readOnly value="draft" steps={[
          { value: "draft", label: "Draft" }, { value: "posted", label: "Posted" }, { value: "cancelled", label: "Cancelled" },
        ]} /> : null}
      </div>
      <p className={cn(textRoleVariants({ role: "meta" }), "mt-2")}>{details.facts}</p>
      {page === "bill" ? <p className={cn(textRoleVariants({ role: "caption" }), "mt-3 flex items-center gap-1 text-warning-text")}>
        <Glyph name="info" />Posting waits for 1 open decision
      </p> : null}
    </header>
  </Spec>;
}

function CommandBand({ page, messageOpen, onMessage, onAside }: {
  page: PageKind; messageOpen: boolean; onMessage: (open: boolean) => void; onAside: (tab: AsideTab) => void;
}) {
  const [following, setFollowing] = useState(page === "bill" || page === "knowledge");
  const [notice, setNotice] = useState("");
  const needs = needsAttention(page);
  const verbs = page === "bill" ? ["Post", "Cancel"] : page === "file" ? ["Download", "Trash"]
    : page === "knowledge" ? ["Edit", "Delete"] : messageOpen ? ["Reply", "Archive"] : ["Compose", "Mark read"];
  const menu = page === "bill" ? ["Pay…", "Reverse", "Send by email", "Download PDF", "Run workflow…"]
    : page === "file" ? ["Read again (import bill)", "Move…", "Rename"] : page === "knowledge" ? ["Move…", "Duplicate", "Export"] : ["Forward", "Mute sender", "Move to…"];
  const download = () => {
    const link = document.createElement("a");
    link.href = `data:application/xml;charset=utf-8,${encodeURIComponent(invoiceXml)}`;
    link.download = bill.file;
    link.click();
  };
  return <Spec owner="CommandBand" className="target-command-band">
    <DropdownMenu.Root>
      <Tooltip label="Record actions"><DropdownMenu.Trigger render={<Button variant="ghost" size="iconSm" aria-label="Record actions" />}><Glyph name="more-vertical" /></DropdownMenu.Trigger></Tooltip>
      <DropdownMenu.Portal><DropdownMenu.Positioner align="start" sideOffset={6}>
        <DropdownMenu.Content className="w-52">{menu.map((label) => <DropdownMenu.Item key={label} onClick={() => setNotice(`${label} — target preview`)}>{label}</DropdownMenu.Item>)}</DropdownMenu.Content>
      </DropdownMenu.Positioner></DropdownMenu.Portal>
    </DropdownMenu.Root>
    {verbs.map((label, index) => <Tooltip key={label} label={label === "Post" ? "Posting waits for 1 open decision" : label}>
      <Button size="sm" variant={index === 0 ? "primary" : "ghost"} disabled={label === "Post"}
        onClick={() => label === "Download" ? download() : setNotice(`${label} — target preview`)}>{label}</Button>
    </Tooltip>)}
    <div className="ml-auto flex items-center gap-1">
      <IconControl icon="triangle-alert" label={`Needs you · ${needs}`} onClick={() => onAside("timeline")} className="relative">
        {needs ? <Chip size="micro" tone={statusTone("waiting")} variant="solid" className="absolute -right-1 -top-1">{needs}</Chip> : null}
      </IconControl>
      <IconControl icon="bell" label={following ? "Following · Unfollow" : "Follow"} aria-pressed={following} onClick={() => setFollowing(!following)} className={following ? "bg-canvas" : undefined} />
      <Popover.Root>
        <Tooltip label="Shared with 3 · Share"><Popover.Trigger render={<Button size="sm" variant="ghost" aria-label="Shared with 3 · Share" className="gap-2 px-1" />}>
          <AvatarStack>{access.map((person) => <Avatar key={person.name} size="sm" initials={person.initials} />)}</AvatarStack><Glyph name="share" />
        </Popover.Trigger></Tooltip>
        <Popover.Portal><Popover.Positioner align="end" sideOffset={6}><Popover.Content className="w-64 p-3">
          <PaneRows><PaneLabel count={3}>Shared with</PaneLabel>{access.map((person) => <li key={person.name}><PaneRow title={person.name} icon="user" sub={person.name === "admin" ? "Owner" : "Can read"} /></li>)}</PaneRows>
        </Popover.Content></Popover.Positioner></Popover.Portal>
      </Popover.Root>
      <span className="mx-2 h-5 w-px bg-border-subtle" />
      <Spec owner="RecordPager · arrow order" className="flex items-center">
        <IconControl icon="chevron-left" label={messageOpen ? "Back to conversations" : "Previous record"} disabled={!messageOpen} onClick={() => onMessage(false)} />
        <span className={cn(textRoleVariants({ role: "caption", numeric: true }), "px-1")}>{page === "bill" ? "1 / 19" : page === "file" ? "4 / 12" : page === "knowledge" ? "2 / 6" : "1 / 38"}</span>
        <IconControl icon="chevron-right" label="Next record" onClick={() => page === "nexus" ? onMessage(true) : setNotice("Next record — target preview")} />
      </Spec>
    </div>
    {notice ? <Popover.Root open onOpenChange={(open) => !open && setNotice("")}>
      <Popover.Trigger render={<span className="absolute right-4 top-10" />} />
      <Popover.Portal><Popover.Positioner sideOffset={4}><Popover.Content className="p-3">
        <p className={textRoleVariants({ role: "description" })}>{notice}</p>
        <Popover.Close render={<Button size="sm" variant="ghost" className="mt-2">Close</Button>} />
      </Popover.Content></Popover.Positioner></Popover.Portal>
    </Popover.Root> : null}
  </Spec>;
}

const billFields = <Group columns={2}>
  <Field name="supplier" label="Supplier" defaultValue={bill.supplier} />
  <Field name="invoiceDate" label="Invoice date" defaultValue={bill.invoiceDate} />
  <Field name="number" label="Bill number" defaultValue={bill.number} />
  <Field name="accountingDate" label="Accounting date" defaultValue={bill.invoiceDate} />
  <Field name="journal" label="Journal" defaultValue={bill.journal} />
  <Field name="currency" label="Currency" defaultValue={bill.currency} />
  <Field name="payment" label="Payment status" defaultValue={bill.payment} />
  <Field name="dueDate" label="Due date" defaultValue={bill.dueDate} />
  <Field name="paymentTerm" label="Payment term" defaultValue="—" />
  <Field name="bankAccount" label="Bank account" defaultValue="—" />
</Group>;
const fileFields = <Group columns={3}>
  <Field name="type" label="Type" defaultValue={fileDetails.type} />
  <Field name="size" label="Size" defaultValue={bill.size} />
  <Field name="format" label="Format" defaultValue={bill.format} />
  <Field name="uploaded" label="Uploaded" defaultValue={fileDetails.uploaded} />
  <Field name="drive" label="Drive" defaultValue={fileDetails.drive} />
  <Field name="folder" label="Folder" defaultValue={fileDetails.folder} />
</Group>;

/** Field and Group declarations use their shipped parser and layout primitives. */
function FieldGrid({ declaration, marks = false }: { declaration: ReactNode; marks?: boolean }) {
  return <>{parsePageGroups(declaration).map((group, index) => <FormGrid key={index} columns={group.columns === 3 ? "three" : "two"} className="gap-x-6 gap-y-5">
    {group.fields.map((field) => <FieldRow key={field.name} label={field.label}
      meta={marks && (field.name === "supplier" || field.name === "number") ? <Chip size="micro" tone="info" variant="soft">{field.name === "supplier" ? "Correction" : "Source"}</Chip> : undefined}>
      <span className={textRoleVariants({ role: "value" })}>{field.name === "payment" ? <Chip size="sm" tone={statusTone("pending")} variant="soft">{bill.payment}</Chip> : String(field.defaultValue)}</span>
    </FieldRow>)}
  </FormGrid>)}</>;
}

function BillFields() {
  const form = useForm<Record<string, unknown>>({ defaultValues: { lines: bill.lines } });
  return <div className="min-w-0 p-5">
    <FieldGrid declaration={billFields} marks />
    <Tabs defaultValue="lines" variant="page" className="mt-8">
      <Tabs.List><Tabs.Tab value="lines">Invoice lines</Tabs.Tab><Tabs.Tab value="journal">Journal items</Tabs.Tab></Tabs.List>
      <Tabs.Panel value="lines" className="pt-3">
        <AppRuntimeProvider runtime={{ widgets: { money: moneyOwner.moneyWidget } }}>
          <EditableLines control={form.control} setValue={form.setValue} name="lines" lines={lineMetadata} readOnly
            primaryFields={["label", "quantity", "unit"]}
            fields={lineMetadata.fields.map((field) => ({ name: field.name, label: field.label, ...(field.name === "unit" ? { widget: "money" } : {}) }))}
            supplementalColumns={[{ key: "subtotal", header: "Subtotal", minWidth: 80, render: (row) => moneyOwner.formatMoney(Number(row.subtotal), "EUR") }]} />
        </AppRuntimeProvider>
        <div className={cn(textRoleVariants({ role: "value" }), "ml-auto mt-5 grid max-w-56 grid-cols-2 gap-x-8 gap-y-2 text-right")}>
          <span className="text-fg-muted">Untaxed</span><span>{bill.amount}</span>
          <span className="text-fg-muted">Tax</span><span>{bill.tax}</span>
          <strong className="border-t border-border-subtle pt-2">Total · EUR</strong><strong className="border-t border-border-subtle pt-2">{bill.amount}</strong>
        </div>
      </Tabs.Panel>
      <Tabs.Panel value="journal" className="pt-4"><PaneRows>
        <PaneLabel>Draft journal items</PaneLabel>
        <li><PaneRow title="Consulting and manuals" sub="Expenses · Debit" meta={bill.amount} icon="accounting" /></li>
        <li><PaneRow title={bill.supplier} sub="Accounts payable · Credit" meta={bill.amount} icon="accounting" /></li>
      </PaneRows></Tabs.Panel>
    </Tabs>
  </div>;
}

function DocumentPane() {
  return <Spec owner="DocumentPane" className="target-document-pane">
    <div className="flex h-9 items-center gap-2 border-b border-border-subtle px-3">
      <Glyph name="file" /><span className={cn(textRoleVariants({ role: "caption", truncate: true }), "min-w-0 flex-1")} title={bill.file}>{bill.file}</span>
      <IconControl icon="arrow-up-right" label="Open source file" onClick={() => window.open("?id=explorations-2026-10-20-record-page-target--file&viewMode=story", "_blank", "noopener")} />
    </div>
    <div className="min-h-0 flex-1 overflow-auto bg-canvas p-3"><InvoicePreview compact /></div>
  </Spec>;
}

/** The future UBL preview provider: actual invoice content, not a placeholder. */
function InvoiceDocument({ compact = false }: { compact?: boolean }) {
  return <Spec owner="PreviewPane · UBL renderer" className={cn("target-invoice-document", compact && "target-invoice-compact")}>
    <article aria-label="Rendered e-invoice" className={textRoleVariants({ role: "description" })}>
      <p className={cn(textRoleVariants({ role: "caption" }), "uppercase tracking-widest")}>{bill.supplier}</p>
      <h2 className={cn(textRoleVariants({ role: "heading" }), "mt-2")}>Invoice</h2>
      <p className={cn(textRoleVariants({ role: "value" }), "mt-1")}>{bill.number}</p>
      <div className="my-5 h-px bg-border-subtle" />
      <div className={compact ? "space-y-5" : "grid grid-cols-2 gap-6"}>
        <div><p className={textRoleVariants({ role: "caption" })}>FROM</p><p className="mt-1 font-medium">{bill.supplier}</p><p className="mt-1 text-fg-muted">{bill.printedAddress.map((line) => <span key={line} className="block">{line}</span>)}</p></div>
        <div><p className={textRoleVariants({ role: "caption" })}>BILL TO · AS PRINTED</p><p className="mt-1 font-medium">{bill.printedBuyer}</p><p className="mt-1 text-fg-muted">Buyer reference · {bill.buyerReference}</p></div>
      </div>
      <dl className="my-5 grid grid-cols-2 gap-y-2 border-y border-border-subtle py-3">
        <dt className="text-fg-muted">Invoice date</dt><dd className="text-right">{bill.invoiceDate}</dd>
        <dt className="text-fg-muted">Due date</dt><dd className="text-right">{bill.dueDate}</dd>
      </dl>
      <PaneRows>{bill.lines.map((line) => <li key={line.id} className="border-b border-border-subtle py-2">
        <p className="font-medium">{line.label}</p>
        <p className="mt-1 flex justify-between gap-2 text-fg-muted"><span>{line.quantity} × {moneyOwner.formatMoney(line.unit, line.currency)}</span><span className="text-fg">{moneyOwner.formatMoney(line.subtotal, line.currency)}</span></p>
      </li>)}</PaneRows>
      <div className="mt-5 flex justify-between"><span className="text-fg-muted">Tax · 0%</span><span>{bill.tax}</span></div>
      <div className={cn(textRoleVariants({ role: "title" }), "mt-3 flex justify-between border-t border-border-strong pt-3")}><span>Total due</span><span>{bill.amount}</span></div>
      <p className={cn(textRoleVariants({ role: "caption" }), "mt-8")}>Structured e-invoice · {bill.format}<br />EUR · Source values preserved</p>
    </article>
  </Spec>;
}

const invoicePreviewProviders: readonly PreviewProvider[] = [{
  id: "target.ubl-preview", mime: "application/xml", priority: 100,
  component: ({ file }) => <InvoiceDocument compact={file.metadata === "compact"} />,
}];

function InvoicePreview({ compact = false }: { compact?: boolean }) {
  return <AppRuntimeProvider runtime={{ previews: invoicePreviewProviders }}><PreviewPane file={{
    name: bill.file, mime: "application/xml",
    url: `data:application/xml;charset=utf-8,${encodeURIComponent(invoiceXml)}`,
    metadata: compact ? "compact" : "full",
  }} /></AppRuntimeProvider>;
}

function FileBody() {
  return <div className="p-6 pt-5">
    <div className="target-file-preview"><InvoicePreview /></div>
    <div className="mt-7"><FormView.SectionHeading label="Details" /><div className="mt-4"><FieldGrid declaration={fileFields} /></div></div>
  </div>;
}

function InboxList({ onOpen }: { onOpen: () => void }) {
  return <div className="p-5">
    <PaneRows>{conversations.map((conversation) => <Fragment key={conversation.id}>
      <PaneLabel>{conversation.day}</PaneLabel>
      <li className="mb-4 rounded-6 border border-border-subtle bg-sheet">
        <PaneRow icon="comments" title={conversation.title} meta={conversation.time} sub={`${conversation.count} ${conversation.count === 1 ? "message" : "messages"} · AP Inbox`}
          onOpen={conversation.id === conversations[0]!.id ? onOpen : undefined} verb={conversation.id === conversations[0]!.id ? "Read message" : undefined} className="px-3 py-3" />
        <p className={cn(textRoleVariants({ role: "description" }), "border-t border-border-subtle px-4 py-4 leading-6")}>{conversation.snippet}</p>
      </li>
    </Fragment>)}</PaneRows>
  </div>;
}

function MessageReader({ onBack }: { onBack: () => void }) {
  return <div className="px-6 pb-8 pt-4">
    <Button size="sm" variant="ghost" onClick={onBack} className="mb-5 -ml-2"><Glyph name="chevron-left" />Conversations</Button>
    <MessageFeed label="Selected message">
      <PaneLabel>Oct 20, 2026</PaneLabel>
      <MessageRow avatar={<Avatar initials="TT" size="md" />} author={message.from}
        meta="08:35 AM" channel={<Chip size="sm" tone="neutral" variant="soft">Email</Chip>}>
        <p className={cn(textRoleVariants({ role: "caption" }), "mb-5")}>From {message.email}<br />To {message.to}</p>
        {message.body}
      </MessageRow>
    </MessageFeed>
  </div>;
}
