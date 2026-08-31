import {describe, expect, it} from 'bun:test';
import {
  availableCommands,
  chatHelpText,
  helpText,
  parseChatCommand,
  parseCommand,
  SLASH_COMMANDS,
  slashCommandRange,
  suggestChatSlashCommands,
  suggestSlashCommands,
} from './commands.js';

describe('parseCommand', () => {
  it('accepts the intentionally small slash-command surface', () => {
    expect(parseCommand('/open-round')).toEqual({openRound: {}});
    expect(parseCommand('/open-round --3')).toEqual({openRound: {round: 3}});
    expect(parseCommand('/open-round 3')).toEqual({openRound: {round: 3}});
    expect(parseCommand('/open-round latest').error).toContain('Unknown round: latest');
    // Visualization commands opt into the right pane through one field.
    expect(parseCommand('/perf')).toMatchObject({
      request: {type: 'query.performance'},
      paneView: 'perf',
    });
    // Modal surfaces stay modal: no pane routing on any of them.
    expect(parseCommand('/help').paneView).toBeUndefined();
    expect(parseCommand('/theme').paneView).toBeUndefined();
    expect(parseCommand('/chat').paneView).toBeUndefined();
    expect(parseCommand('/nope').paneView).toBeUndefined();
    expect(parseCommand('/perf')).toMatchObject({
      request: {type: 'query.performance'},
      responseView: 'perf',
    });
    expect(parseCommand('/chat what changed in the latest round?')).toEqual({
      localView: 'chat',
      chatMessage: 'what changed in the latest round?',
    });
  });

  it('parses run-control commands', () => {
    expect(parseCommand('/pause')).toEqual({request: {type: 'command.pause'}});
    expect(parseCommand('/resume')).toEqual({request: {type: 'command.resume'}});
    expect(parseCommand('/steer prioritize the KV cache path')).toEqual({
      request: {type: 'command.steer', text: 'prioritize the KV cache path'},
    });
  });

  it('requires a message for /steer', () => {
    expect(parseCommand('/steer').error).toContain('Usage: /steer');
    expect(parseCommand('/steer   ').error).toContain('Usage: /steer');
  });

  it('rejects text that belongs to Experiment chat', () => {
    expect(parseCommand('what is happening?')).toEqual({
      error: 'Commands start with /. Use Experiment chat for questions.',
    });
    expect(parseCommand('')).toEqual({error: 'Enter a slash command. Use /help.'});
  });

  it('rejects the removed experiment-log commands', () => {
    expect(parseCommand('/history').error).toContain('Unknown command: /history');
    expect(parseCommand('/history rounds').error).toContain('Unknown command: /history rounds');
    expect(parseCommand('/experiments').error).toContain('Unknown command: /experiments');
  });

  it('keeps inspection commands out of the public command surface', () => {
    expect(parseCommand('/round 4').error).toContain('Unknown command');
    expect(parseCommand('/invocation abc').error).toContain('Unknown command');
    expect(parseCommand('/show workspace/file').error).toContain('Unknown command');
  });

  it('provides local help without a backend request', () => {
    expect(parseCommand('/help')).toEqual({localView: 'help'});
  });

  it('opens chat without requiring an initial question', () => {
    expect(parseCommand('/chat')).toEqual({localView: 'chat'});
    expect(parseCommand('/chat   ')).toEqual({localView: 'chat'});
  });

  it('lists themes bare and selects a known theme by name', () => {
    expect(parseCommand('/theme')).toEqual({localView: 'theme'});
    expect(parseCommand('/theme   ')).toEqual({localView: 'theme'});
    expect(parseCommand('/theme solarized-light')).toEqual({
      localView: 'theme',
      themeName: 'solarized-light',
    });
  });

  it('rejects an unknown theme name with the available list', () => {
    const parsed = parseCommand('/theme monokai');
    expect(parsed.error).toContain('Unknown theme: monokai');
    expect(parsed.error).toContain('catppuccin-mocha');
    expect(parsed.localView).toBeUndefined();
  });
});

describe('command surface by view', () => {
  it('drops /chat where the chat is already a pane of the view', () => {
    const docked = availableCommands({chatDocked: true}).map(command => command.name);

    expect(docked).not.toContain('/chat');
    expect(docked).toContain('/perf');
    expect(helpText({chatDocked: true})).not.toMatch(/\/chat\s/);
    expect(suggestSlashCommands('/c', {chatDocked: true}).map(command => command.name)).toEqual([]);
  });

  it('offers /chat everywhere the chat is not already on screen', () => {
    expect(availableCommands().map(command => command.name)).toContain('/chat');
    expect(helpText()).toMatch(/\/chat\s/);
    expect(suggestSlashCommands('/c').map(command => command.name)).toEqual(['/chat']);
  });

  it('keeps chat control out of the global command surface', () => {
    // Chat is controlled from the chat composer, so the global registry
    // carries no thread commands at all and never claims to.
    const names = SLASH_COMMANDS.map(command => command.name);
    expect(names).not.toContain('/new-chat');
    expect(names).not.toContain('/chats');
    expect(names).not.toContain('/model');
    expect(names).not.toContain('/clear');
    expect(helpText()).not.toContain('/new-chat');
    expect(helpText()).not.toContain('/chats');
    expect(parseCommand('/new-chat').error).toContain('Unknown command');
    expect(parseCommand('/chats').error).toContain('Unknown command');
  });

  it('reaches the todo and prompt toggles by name', () => {
    // macOS keeps the function keys for itself and a terminal may keep a
    // Control chord, so both toggles have to be reachable without either.
    expect(parseCommand('/todos')).toEqual({toggle: 'todos'});
    expect(parseCommand('/prompt')).toEqual({toggle: 'prompt'});
  });

  it('still accepts /chat when it is not offered, since the chat is the point', () => {
    // Hidden from the list, not removed from the client.
    expect(parseCommand('/chat')).toEqual({localView: 'chat'});
  });
});

describe('chat composer commands', () => {
  it('resolves the chat-scoped commands', () => {
    expect(parseChatCommand('/clear')).toEqual({command: 'clear'});
    expect(parseChatCommand('/model')).toEqual({command: 'model'});
    expect(parseChatCommand('/resume')).toEqual({command: 'resume'});
  });

  it('shadows /resume: in the chat it resumes a thread, not the run', () => {
    // The global surface keeps /resume for a paused run; the composer's own
    // command wins where it was typed, so the two never compete.
    expect(parseChatCommand('/resume').command).toBe('resume');
    expect(parseCommand('/resume')).toEqual({request: {type: 'command.resume'}});
  });

  it('forwards global commands the composer has always accepted', () => {
    expect(parseChatCommand('/pause')).toEqual({global: true});
    expect(parseChatCommand('/steer look at the cache')).toEqual({global: true});
    expect(parseChatCommand('/perf')).toEqual({global: true});
  });

  it('answers unknown slash input with the chat help, not the global error', () => {
    const parsed = parseChatCommand('/threads');
    expect(parsed.command).toBeUndefined();
    expect(parsed.global).toBeUndefined();
    expect(parsed.help).toBe(chatHelpText());
    expect(parsed.help).toContain('/model');
    expect(parsed.help).toContain('/resume');
    expect(parsed.help).not.toContain('Unknown command');
  });

  it('suggests only the chat commands from a slash prefix', () => {
    expect(suggestChatSlashCommands('/').map(command => command.name)).toEqual([
      '/clear',
      '/model',
      '/resume',
    ]);
    expect(suggestChatSlashCommands('/m').map(command => command.name)).toEqual(['/model']);
    expect(suggestChatSlashCommands('/pa')).toEqual([]);
    expect(suggestChatSlashCommands('/model ')).toEqual([]);
    expect(suggestChatSlashCommands('what changed?')).toEqual([]);
  });
});

describe('slash-command input helpers', () => {
  it('suggests available commands from a slash prefix', () => {
    expect(suggestSlashCommands('/').map(command => command.name)).toEqual([
      '/help',
      '/chat',
      '/pause',
      '/resume',
      '/steer',
      '/open-round',
      '/perf',
      '/todos',
      '/prompt',
      '/theme',
    ]);
    expect(suggestSlashCommands('/h').map(command => command.name)).toEqual(['/help']);
    expect(suggestSlashCommands('/e')).toEqual([]);
    expect(suggestSlashCommands('/open').map(command => command.name)).toEqual(['/open-round']);
    expect(suggestSlashCommands('/perf ')).toEqual([]);
    expect(suggestSlashCommands('perf')).toEqual([]);
  });

  it('finds a leading slash-command token for syntax highlighting', () => {
    expect(slashCommandRange('/open-round')).toEqual({start: 0, end: 11});
    expect(slashCommandRange('/steer inspect the cache')).toEqual({start: 0, end: 6});
    expect(slashCommandRange('/')).toBeNull();
    expect(slashCommandRange('show /perf')).toBeNull();
  });
});
