import type { ReactNode } from 'react';

export function Page({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <>
      <h1 tabIndex={-1}>{title}</h1>
      {children}
    </>
  );
}

export function Problem({ message }: { message: string | null }) {
  return message ? <p role="alert" className="problem">{message}</p> : null;
}
