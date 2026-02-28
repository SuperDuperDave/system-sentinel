/**
 * Parses a date string that may be in ISO format or WCF JSON format (\/Date(TIMESTAMP)\/).
 * @param dateStr The date string to parse.
 * @returns A Date object, or an Invalid Date object if parsing fails.
 */
export function parseDate(dateStr: string | null | undefined): Date {
    if (!dateStr) return new Date(NaN);

    // Handle WCF JSON date format: /Date(1234567890)/
    const wcfMatch = dateStr.match(/\/Date\((\d+)\)\//);
    if (wcfMatch) {
        return new Date(parseInt(wcfMatch[1], 10));
    }

    // Default to standard Date constructor for ISO and other formats
    return new Date(dateStr);
}

/**
 * Formats a date to a locale-aware string.
 * @param date The date to format.
 * @returns A formatted date string.
 */
export function formatDateTime(date: Date): string {
    if (isNaN(date.getTime())) return "Invalid Date";
    return date.toLocaleString([], {
        month: '2-digit',
        day: '2-digit',
        year: 'numeric',
        hour: '2-digit',
        minute: '2-digit'
    });
}
