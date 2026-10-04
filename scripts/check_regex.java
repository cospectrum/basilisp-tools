import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.regex.Pattern;

// Execute the JDK's own Pattern implementation against generated fixtures.
class RegexOracle {
    private static String decode(String value) {
        return new String(Base64.getDecoder().decode(value), StandardCharsets.UTF_8);
    }
    private static String encode(String value) {
        return Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }
    public static void main(String[] args) throws IOException {
        BufferedReader input = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        for (String line; (line = input.readLine()) != null;) {
            String[] fields = line.split("\\t", -1);
            try {
                var pattern = Pattern.compile(decode(fields[0]));
                var text = decode(fields[1]);
                var full = pattern.matcher(text).matches();
                var found = pattern.matcher(text);
                System.out.println((full ? "1" : "0") + "\t" +
                    (found.find() ? "1\t" + encode(found.group()) : "0\t"));
            } catch (Exception error) {
                System.out.println("error\t" + encode(error.getMessage()));
            }
        }
    }
}
