export const PROCESS_PATTERN = /graduad|transfer[eê]ncia\s+facultativa|vagas\s+remanescentes/i;

/** @param {{value: string, label: string}} option */
export function isProcessOption(option) {
  return Boolean(option.value) && PROCESS_PATTERN.test(option.label);
}

/**
 * @param {Array<{index: number, name: string, options: Array<{value: string, label: string}>}>} selects
 * @returns {Array<{selectIndex: number, select: string, value: string, label: string}>}
 */
export function processOptions(selects) {
  return selects.flatMap((sel) =>
    sel.options
      .filter(isProcessOption)
      .map((opt) => ({ selectIndex: sel.index, select: sel.name, value: opt.value, label: opt.label })),
  );
}

export const SUBMIT_SELECTOR = 'button[type="submit"], input[type="submit"], button:has-text("Buscar")';
