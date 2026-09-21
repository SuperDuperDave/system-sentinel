import { useEffect, useRef, useState } from 'react';
import { CatalogEntry, Unauthorized, catalog } from '../api';
import { CopyButton } from '../Copy';
import { Head, RowList } from '../Sections';
import { useApp } from '../store';
import styles from './Agents.module.css';

/**
 * Agents: how a local agent reaches this machine.
 *
 * The page a person opens once, to wire an agent up and never think about it again. It says
 * where the token lives and never shows it — the token is the whole boundary, and a page that
 * prints it puts it in every screenshot of this dashboard. The tool list is the catalog the
 * server publishes, not a list kept here, so it cannot fall out of step with what the agent
 * will actually find.
 */
const RELEASES = 'https://github.com/SuperDuperDave/system-sentinel/releases/latest';

export function Agents() {
  const [tools, setTools] = useState<CatalogEntry[]>([]);
  const [version, setVersion] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const setSession = useApp((s) => s.setSession);
  const origin = window.location.origin;

  useEffect(() => {
    catalog()
      .then((body) => {
        setTools(body.readings);
        setVersion(body.version);
      })
      .catch((err: unknown) => {
        if (err instanceof Unauthorized) return setSession('closed');
        setProblem(err instanceof Error ? err.message : String(err));
      });
  }, [setSession]);

  return (
    <section>
      <Head title="Agents" />
      <p className={styles.lede}>
        An agent on this machine reads it through the same boundary this dashboard does: one address, one token, and every reading below as a tool.
        What an agent receives is the envelope a person sees here, redaction included.
      </p>
      {/* The version of the process answering, not of a file on disk, and shown only once it has
          answered: an unknown version is worth less than nothing. The link is the person looking;
          the tool asks the network for nothing. */}
      {version ? (
        <p className={`${styles.version} readout`}>
          version {version} · <a href={RELEASES} target="_blank" rel="noreferrer">releases</a>
        </p>
      ) : null}

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>The token</h2>
        <p className={styles.what}>
          The tool made a token the first time it ran and keeps it in its data directory, in a file called <code className={styles.inline}>token</code> —
          under <code className={styles.inline}>%LOCALAPPDATA%\SystemSentinel\</code> on Windows, and <code className={styles.inline}>~/.system-sentinel/</code> elsewhere.
          Nothing on this page shows it. Every request carries it as <code className={styles.inline}>Authorization: Bearer …</code>, or as the session cookie this dashboard holds.
        </p>
        <Command text="system-sentinel token" what="Print it on the machine:" />
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>Registering it</h2>
        <Command
          text={`claude mcp add --transport http system-sentinel ${origin}/mcp --header "Authorization: Bearer $(system-sentinel token)"`}
          what="Claude Code, in one line:"
        />
        <Command
          text={`codex mcp add system-sentinel --url ${origin}/mcp --bearer-token-env-var SYSTEM_SENTINEL_TOKEN`}
          what="Codex, which reads the token from the environment rather than from a header: set SYSTEM_SENTINEL_TOKEN to the contents of the token file in the environment Codex starts in."
        />
        <Command text={`${origin}/mcp`} what="Any other MCP client: streamable HTTP at this address, with the same Authorization header." />
        <Command
          text={`curl -H "Authorization: Bearer $(system-sentinel token)" ${origin}/api/readings/events`}
          what="Without MCP, any shell reads the same evidence:"
        />
        <p className={styles.note}>
          That is the address this page was reached at. The server binds to the loopback address of the machine it reads; an agent running beside it can use that directly.
        </p>
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>The interface</h2>
        <p className={styles.what}>
          <a href="/api/docs" target="_blank" rel="noreferrer">The API document</a> is live at <code className={styles.inline}>/api/docs</code>, and its
          machine-readable form at <code className={styles.inline}>/api/openapi.json</code>. Beside the readings, the stack is reachable as tools of its
          own — <code className={styles.inline}>stack_list</code>, <code className={styles.inline}>stack_add</code>, <code className={styles.inline}>stack_remove</code>,{' '}
          <code className={styles.inline}>stack_clear</code>, <code className={styles.inline}>prompts_list</code> and <code className={styles.inline}>compose</code> — so an
          agent composes the same handoff a person copies here, and needs no clipboard to read it.
        </p>
      </div>

      <div className={styles.block}>
        <h2 className={`${styles.blockTitle} label`}>The tools</h2>
        {problem ? <p className={`${styles.problem} readout`}>The catalog could not be read: {problem}</p> : null}
        <p className={styles.what}>Every reading is one tool, named as it is named here. A heavy one takes seconds of the machine's attention.</p>
        <RowList
          items={tools}
          idOf={(tool) => tool.name}
          layout={styles.toolRow}
          cells={(tool) => (
            <>
              <span className={`${styles.toolName} readout`}>{tool.name}</span>
              {tool.heavy ? <span className={`${styles.heavy} label`}>heavy</span> : null}
              <span className={styles.toolWhat}>{tool.description}</span>
            </>
          )}
          inspect={(tool) => (
            <div className={styles.toolDetail}>
              <p className={styles.toolFull}>{tool.description}</p>
              <p className={`${styles.toolMeta} readout`}>
                {tool.params.length
                  ? `parameters: ${tool.params.map((param) => `${param.name}${param.default === null || param.default === undefined ? '' : ` (${String(param.default)})`}`).join(', ')}`
                  : 'no parameters'}
              </p>
              <p className={`${styles.toolMeta} readout`}>classes: {tool.classes.join(', ')}</p>
              {tool.private.length ? <p className={`${styles.toolMeta} readout`}>redaction removes: {tool.private.join(', ')}</p> : null}
            </div>
          )}
        />
      </div>
    </section>
  );
}

/** One line to run, with the fallback the clipboard needs where it is not granted. */
function Command({ text, what }: { text: string; what: string }) {
  const held = useRef<HTMLElement>(null);
  return (
    <div className={styles.command}>
      <p className={styles.what}>{what}</p>
      <div className={styles.commandRow}>
        <code className={`${styles.code} readout`} ref={held}>{text}</code>
        <CopyButton text={text} selectRef={held} />
      </div>
    </div>
  );
}
