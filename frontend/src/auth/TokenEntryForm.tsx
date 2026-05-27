import type { ReactNode } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const tokenSchema = z.object({
  token: z.string().trim().min(1, "Enter the access token"),
});

type TokenForm = z.infer<typeof tokenSchema>;

interface TokenEntryFormProps {
  /** True when the previous token was rejected by the server. */
  rejected: boolean;
  onSubmit: (token: string) => void;
}

/**
 * The token-entry gate. A single masked field; on submit the token is stored in
 * sessionStorage and the app mounts. If the entered token is wrong, the first
 * authenticated call returns 401 / the WS closes 1008, which clears it and
 * returns here with `rejected` set.
 */
export function TokenEntryForm({ rejected, onSubmit }: TokenEntryFormProps): ReactNode {
  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<TokenForm>({
    resolver: zodResolver(tokenSchema),
    defaultValues: { token: "" },
  });

  return (
    <div className="gate">
      <form
        className="gate__card"
        onSubmit={handleSubmit((values) => onSubmit(values.token.trim()))}
        noValidate
      >
        <h1 className="gate__title">Johnny&nbsp;5</h1>
        <p className="gate__sub">Enter the access token to attach to him.</p>

        {rejected ? (
          <p className="gate__error" role="alert">
            That token was rejected. Try again.
          </p>
        ) : null}

        <label className="gate__label" htmlFor="token">
          Access token
        </label>
        <input
          id="token"
          type="password"
          autoComplete="off"
          autoFocus
          className="gate__input"
          aria-invalid={errors.token ? "true" : undefined}
          {...register("token")}
        />
        {errors.token ? (
          <p className="gate__field-error" role="alert">
            {errors.token.message}
          </p>
        ) : null}

        <button type="submit" className="gate__submit" disabled={isSubmitting}>
          Attach
        </button>
      </form>
    </div>
  );
}
