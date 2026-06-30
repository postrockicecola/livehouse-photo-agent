/** Shared Workbench / Studio shell width — scales on ultra-wide without huge side gutters. */
export const STUDIO_SHELL_MAX = "max-w-[min(100%,118rem)]";

export const STUDIO_SHELL_PX = "px-[clamp(1rem,2.5vw,3.5rem)]";

export const STUDIO_SHELL_INNER = `mx-auto w-full min-w-0 ${STUDIO_SHELL_MAX} ${STUDIO_SHELL_PX}`;

/** Cancel parent horizontal padding for edge-to-edge bands inside the shell. */
export const STUDIO_SHELL_BLEED_X = "-mx-[clamp(1rem,2.5vw,3.5rem)]";

/** Sticky sidebar top offset (nav height + gap). */
export const STUDIO_SIDEBAR_STICKY = "xl:sticky xl:top-[4.75rem] xl:max-h-[calc(100dvh-5.75rem)] xl:overflow-y-auto xl:overscroll-contain";
