package ai.satoris.amplifai.phone;

import java.nio.ByteBuffer;
import java.nio.charset.CharacterCodingException;
import java.nio.charset.CodingErrorAction;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Bounded JSON for the setup contract; rejects duplicate keys and Android's lenient JSON extensions. */
final class SetupJson {
    private final String text;
    private int offset;

    private SetupJson(String text) { this.text = text; }

    static Map<String, Object> parse(byte[] bytes, int limit) {
        if (bytes.length == 0 || bytes.length > limit) throw invalid();
        try {
            String text = StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                    .onUnmappableCharacter(CodingErrorAction.REPORT).decode(ByteBuffer.wrap(bytes)).toString();
            SetupJson parser = new SetupJson(text);
            Object result = parser.value(0);
            parser.space();
            if (parser.offset != text.length() || !(result instanceof Map)) throw invalid();
            @SuppressWarnings("unchecked") Map<String, Object> object = (Map<String, Object>) result;
            return object;
        } catch (CharacterCodingException malformed) { throw invalid(); }
    }

    static String encode(Object value) {
        if (value == null) return "null";
        if (value instanceof String string) {
            StringBuilder result = new StringBuilder("\"");
            for (int i = 0; i < string.length(); i++) {
                char c = string.charAt(i);
                if (c == '"' || c == '\\') result.append('\\').append(c);
                else if (c < 0x20) {
                    result.append("\\u00").append("0123456789abcdef".charAt(c >>> 4))
                            .append("0123456789abcdef".charAt(c & 15));
                } else result.append(c);
            }
            return result.append('"').toString();
        }
        if (value instanceof Integer || value instanceof Long || value instanceof Boolean) return value.toString();
        StringBuilder result = new StringBuilder(value instanceof Map ? "{" : "[");
        boolean first = true;
        if (value instanceof Map<?, ?> map) {
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                if (!(entry.getKey() instanceof String)) throw invalid();
                if (!first) result.append(',');
                first = false;
                result.append(encode(entry.getKey())).append(':').append(encode(entry.getValue()));
            }
            return result.append('}').toString();
        }
        if (value instanceof List<?> list) {
            for (Object item : list) {
                if (!first) result.append(',');
                first = false;
                result.append(encode(item));
            }
            return result.append(']').toString();
        }
        throw invalid();
    }

    static Map<String, Object> object(Object... pairs) {
        Map<String, Object> result = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) result.put((String) pairs[i], pairs[i + 1]);
        return result;
    }

    private Object value(int depth) {
        if (depth > 8) throw invalid();
        space();
        if (offset == text.length()) throw invalid();
        char c = text.charAt(offset);
        if (c == '"') return string();
        if (c == '{') {
            offset++;
            Map<String, Object> result = new LinkedHashMap<>();
            if (take('}')) return result;
            do {
                space();
                String key = string();
                if (result.containsKey(key) || result.size() >= 64 || !take(':')) throw invalid();
                result.put(key, value(depth + 1));
                if (take('}')) return result;
            } while (take(','));
            throw invalid();
        }
        if (c == '[') {
            offset++;
            List<Object> result = new ArrayList<>();
            if (take(']')) return result;
            do {
                if (result.size() >= 64) throw invalid();
                result.add(value(depth + 1));
                if (take(']')) return result;
            } while (take(','));
            throw invalid();
        }
        for (String literal : new String[]{"true", "false", "null"}) {
            if (text.startsWith(literal, offset)) {
                offset += literal.length();
                return literal.equals("null") ? null : literal.equals("true");
            }
        }
        // The contract's only numeric field is the integer capabilities schema.
        int start = offset;
        if (c == '-') offset++;
        if (offset == text.length() || !digit(text.charAt(offset))) throw invalid();
        if (text.charAt(offset++) != '0') while (offset < text.length() && digit(text.charAt(offset))) offset++;
        try { return Long.parseLong(text.substring(start, offset)); }
        catch (NumberFormatException malformed) { throw invalid(); }
    }

    private String string() {
        if (offset == text.length() || text.charAt(offset++) != '"') throw invalid();
        StringBuilder result = new StringBuilder();
        while (offset < text.length()) {
            char c = text.charAt(offset++);
            if (c == '"') {
                for (int i = 0; i < result.length(); i++) {
                    char code = result.charAt(i);
                    if (Character.isHighSurrogate(code)) {
                        if (++i == result.length() || !Character.isLowSurrogate(result.charAt(i))) throw invalid();
                    } else if (Character.isLowSurrogate(code)) throw invalid();
                }
                return result.toString();
            }
            if (c < 0x20) throw invalid();
            if (c == '\\') {
                if (offset == text.length()) throw invalid();
                c = text.charAt(offset++);
                switch (c) {
                    case '"', '\\', '/' -> result.append(c);
                    case 'b' -> result.append('\b');
                    case 'f' -> result.append('\f');
                    case 'n' -> result.append('\n');
                    case 'r' -> result.append('\r');
                    case 't' -> result.append('\t');
                    case 'u' -> {
                        if (text.length() - offset < 4) throw invalid();
                        int code = 0;
                        for (int i = 0; i < 4; i++) {
                            int hex = "0123456789abcdef".indexOf(Character.toLowerCase(text.charAt(offset++)));
                            if (hex < 0) throw invalid();
                            code = code * 16 + hex;
                        }
                        result.append((char) code);
                    }
                    default -> throw invalid();
                }
            } else result.append(c);
        }
        throw invalid();
    }

    private boolean take(char token) {
        space();
        if (offset == text.length() || text.charAt(offset) != token) return false;
        offset++;
        return true;
    }

    private void space() {
        while (offset < text.length() && " \r\n\t".indexOf(text.charAt(offset)) >= 0) offset++;
    }
    private static boolean digit(char c) { return c >= '0' && c <= '9'; }
    private static IllegalArgumentException invalid() { return new IllegalArgumentException("setup_json_invalid"); }
}
