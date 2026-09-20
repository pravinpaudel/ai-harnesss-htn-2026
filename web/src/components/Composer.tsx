import { FormEvent, KeyboardEvent, MutableRefObject, useEffect, useRef } from "react";
import { Send } from "lucide-react";

interface Props {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  busy: boolean;
  /** Optional handle so the library can focus the box after filling it. */
  inputRef?: MutableRefObject<HTMLTextAreaElement | null>;
}

export default function Composer({ value, onChange, onSubmit, busy, inputRef: externalRef }: Props) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const inputRef = externalRef ?? localRef;

  useEffect(() => {
    const textarea = inputRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 180)}px`;
  }, [value]);

  function submit(event: FormEvent) {
    event.preventDefault();
    onSubmit();
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSubmit();
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <label className="sr-only" htmlFor="question">
        Research question
      </label>
      <textarea
        id="question"
        ref={inputRef}
        rows={2}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask about the loaded corpus — answers cite the exact lines they come from"
      />
      <button type="submit" disabled={busy || value.trim().length === 0} aria-label="Ask question">
        <Send size={18} aria-hidden="true" />
      </button>
    </form>
  );
}
