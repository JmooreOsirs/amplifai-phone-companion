import Foundation
var alpha: [UInt32] = [], extend: [UInt32] = [], joined: [UInt32] = [], prepend: [UInt32] = []
for value in 0...0x10ffff {
 guard let scalar = UnicodeScalar(value) else { continue }
 if scalar.properties.isAlphabetic { alpha.append(scalar.value) }
 if scalar.properties.isGraphemeExtend { extend.append(scalar.value) }
 if ("a" + String(scalar)).count == 1 { joined.append(scalar.value) }
 if (String(scalar) + "a").count == 1 && !Character(String(scalar)).isLetter && !Character(String(scalar)).isNumber { prepend.append(scalar.value) }
}
FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject:["alpha":alpha,"extend":extend,"joined":joined,"prepend":prepend]))
